"""Command line entry point.

    python3 -m cnclog                          normal çalışma (arayüz açılır)
    python3 -m cnclog --surucu simulator       simülatörle dene
    python3 -m cnclog --test-baglanti          tezgaha bağlan, tek okuma göster
    python3 -m cnclog --rapor bugun            raporu ekrana bas ve çık

Options are Turkish because the people running this read Turkish; the code
behind them is English like everything else.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from typing import Optional

from . import __version__, report
from .app import AlreadyRunning, build_application
from .drivers.base import DriverError
from .drivers.registry import DRIVER_LABELS, available_drivers
from .model import STATE_LABELS, MachineState, format_number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cnclog",
        description="CNC Log System — Heidenhain TNC 640 veri kayıt ve raporlama",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Kullanılabilir sürücüler:\n"
            + "\n".join(
                f"  {name:<18} {DRIVER_LABELS.get(name, '')}"
                for name in available_drivers()
            )
        ),
    )
    parser.add_argument("--surucu", "-s", metavar="AD",
                        help="Sürücü seçimi (config.ini'deki ayarı geçersiz kılar)")
    parser.add_argument("--config", "-c", metavar="YOL",
                        help="config.ini dosyasının yolu")
    parser.add_argument("--dizin", "-d", metavar="YOL",
                        help="Çalışma dizini (veri buraya yazılır)")
    parser.add_argument("--port", "-p", type=int, metavar="N",
                        help="Web arayüzü portu")
    parser.add_argument("--web-yok", action="store_true",
                        help="Arayüzü başlatma, sadece kayıt tut")
    parser.add_argument("--tarayici-yok", action="store_true",
                        help="Arayüzü başlat ama tarayıcıyı açma")
    parser.add_argument("--sim-hiz", type=float, metavar="X", default=1.0,
                        help="Simülatör hız çarpanı (60 = 1 saat 1 dakikada)")
    parser.add_argument("--sim-seed", type=int, metavar="N",
                        help="Simülatör rastgelelik tohumu (tekrarlanabilir senaryo)")
    parser.add_argument("--rapor", nargs="?", const="bugun", metavar="TARİH",
                        help="Raporu ekrana bas ve çık (bugun | dun | YYYY-AA-GG)")
    parser.add_argument("--test-baglanti", action="store_true",
                        help="Tezgaha bağlan, tek okuma yap, sonucu göster ve çık")
    parser.add_argument("--tara", action="store_true",
                        help="Ağda Heidenhain kontrol ara ve bulunanları listele")
    parser.add_argument("--surum", action="version", version=f"cnclog {__version__}")
    return parser


# --------------------------------------------------------------------- browser


def _tarayici_adaylari() -> list:
    """Chromium-family browsers to try, in order, for this platform."""
    adaylar = [
        shutil.which(isim)
        for isim in (
            "chromium", "chromium-browser", "google-chrome",
            "google-chrome-stable", "brave-browser", "microsoft-edge",
            "qupzilla", "falkon",
        )
    ]
    if os.name == "nt":
        # Windows browsers are not on PATH; check the usual install locations.
        program_files = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        goreli = [
            r"Google\Chrome\Application\chrome.exe",
            r"Microsoft\Edge\Application\msedge.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe",
        ]
        for kok in program_files:
            if not kok:
                continue
            for parca in goreli:
                adaylar.append(os.path.join(kok, parca))
    return [a for a in adaylar if a and os.path.exists(a)]


def _tarayici_ac(url: str) -> None:
    """Open the UI, preferring app mode so it looks like a real window.

    A Chromium-family window started with --app has no address bar and shows up
    in the task list as its own window, which is what "alt-tab edip
    bakabileceğimiz bir program" actually means in practice.
    """
    for yol in _tarayici_adaylari():
        try:
            subprocess.Popen(
                [yol, f"--app={url}", "--window-size=1280,860"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except OSError:
            continue
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a missing browser is not fatal
        pass


# ---------------------------------------------------------------------- modes


def _rapor_calistir(args, tarih_metni: str) -> int:
    # writer=False: reports must be runnable while the logger is recording.
    app = build_application(
        config_path=args.config,
        base_dir=args.dizin,
        driver_name=args.surucu,
        sim_speed=args.sim_hiz,
        sim_seed=args.sim_seed,
        writer=False,
    )
    try:
        gun = report.parse_day(tarih_metni)
    except ValueError:
        print(f"Geçersiz tarih: {tarih_metni}  (bugun | dun | YYYY-AA-GG)",
              file=sys.stderr)
        app.close_readonly()
        return 2

    ts_from, ts_to = report.day_bounds(gun)
    ozet = report.summarize(app.storage, app.cfg, ts_from, ts_to)
    print(report.render_text(ozet, f"GÜN RAPORU — {gun.strftime('%d.%m.%Y')}"))

    vardiyalar = report.shift_bounds(app.cfg, gun)
    if len(vardiyalar) > 1:
        print("-" * 68)
        print("  VARDİYALAR")
        print("-" * 68)
        print(f"  {'Vardiya':<16}{'Çalışma':>12}{'Duruş':>12}{'Kullanım':>12}")
        for etiket, baslangic, bitis in vardiyalar:
            v = report.summarize(app.storage, app.cfg, baslangic, bitis)
            kullanim = f"%{v['availability']:.1f}" if v["availability"] else "—"
            print(
                f"  {etiket:<16}"
                f"{report.format_duration(v['run_s']):>12}"
                f"{report.format_duration(v['down_s']):>12}"
                f"{kullanim:>12}"
            )
        print()

    app.close_readonly()
    return 0


def _tara(args) -> int:
    """Look for a control and report, without touching the database."""
    from . import discovery
    from .config import load_config

    cfg = load_config(args.config, base_dir=args.dizin or os.getcwd())
    port = cfg.tnc_port or discovery.LSV2_PORT

    print("Heidenhain kontrol aranıyor…")
    print(f"  Port          : {port} (LSV2)")
    print(f"  Yapılandırılan: {cfg.tnc_ip or '(yok — otomatik aranacak)'}")
    adresler = discovery.local_addresses()
    print(f"  Bu makinenin adresleri: {', '.join(adresler) or '(bulunamadı)'}")
    print("-" * 60)

    bulunan = discovery.find_control(
        configured_ip=cfg.tnc_ip or None,
        port=port,
        timeout=cfg.timeout_s,
        scan_subnet=cfg.auto_scan,
        on_progress=lambda m: print(f"  {m}"),
    )
    print("-" * 60)

    if bulunan is None:
        print("\nHiçbir Heidenhain kontrol bulunamadı.\n")
        print("EN SIK SEBEP — tezgahta dış erişim kapalı:")
        print("  Kontrol ekranında Programlama moduna geçin, MOD tuşuna basın,")
        print("  'External access' ayarını ON yapın, END ile çıkın.")
        print("  (Bu ayar TNC 640 Programming Station simülatöründe de gerekir.)")
        print("\nDiğer kontroller:")
        print("  - Tezgah/simülatör açık mı, PLC programı çalışıyor mu?")
        print(f"  - Bu makine tezgahla aynı ağda mı? (port {port})")
        print("  - Simülatör aynı bilgisayarda ise 127.0.0.1 denenmiş olmalı;")
        print("    yukarıdaki listede göründüğünden emin olun.")
        print("  - Adresi biliyorsanız config.ini içine yazın:")
        print("        [surucu]")
        print("        tnc_ip = 192.168.1.50")
        return 1

    print(f"\nBULUNDU: {bulunan}")
    print("\nBu adres otomatik kullanılacak; config.ini'ye yazmanız gerekmez.")
    print("Sabitlemek isterseniz:")
    print("        [surucu]")
    print(f"        tnc_ip = {bulunan.host}")
    if discovery.port_open(bulunan.host, discovery.OPCUA_PORT, timeout=1.0):
        print(f"\nNot: {bulunan.host}:{discovery.OPCUA_PORT} de açık — OPC UA NC")
        print("Server (Opsiyon 56) etkin olabilir. Gerçek F/S değerleri için")
        print("bunu kullanabilirsiniz, bkz. KURULUM.md.")
    return 0


def _test_baglanti(args) -> int:
    """Connect once and print what the control actually exposes.

    This is the first thing to run on the shop floor: it answers "does the
    connection work, and which fields does this particular machine give us"
    without writing anything or starting the logger.
    """
    try:
        app = build_application(
            config_path=args.config,
            base_dir=args.dizin,
            driver_name=args.surucu,
            sim_speed=args.sim_hiz,
            sim_seed=args.sim_seed,
            writer=False,
        )
    except DriverError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1

    cfg = app.cfg
    print(f"Sürücü      : {app.driver.describe()}")
    if cfg.driver not in ("simulator", "auto"):
        print(f"Adres       : {cfg.tnc_ip}:{cfg.tnc_port}")
    elif cfg.driver == "auto":
        print(f"Adres       : otomatik aranacak"
              + (f" (önce {cfg.tnc_ip})" if cfg.tnc_ip else ""))
    print(f"Zaman aşımı : {cfg.timeout_s:g} sn")
    print("-" * 60)

    # Discovery can take a few seconds; show it happening rather than hanging.
    if hasattr(app.driver, "progress_callback"):
        app.driver.progress_callback = lambda m: print(f"  {m}")

    try:
        app.driver.connect()
    except (DriverError, OSError) as exc:
        print(f"BAĞLANTI KURULAMADI: {exc}", file=sys.stderr)
        app.close_readonly()
        return 1

    print("Bağlantı kuruldu. Tek okuma yapılıyor…\n")
    try:
        snap = app.driver.read()
    except (DriverError, OSError) as exc:
        print(f"OKUMA BAŞARISIZ: {exc}", file=sys.stderr)
        app.driver.disconnect()
        app.close_readonly()
        return 1

    durum = app.collector.tracker.derive_state(snap)
    alanlar = [
        ("Çalışma durumu (ham)", snap.exec_state.value),
        ("Türetilen durum", STATE_LABELS[durum]),
        ("İlerleme F (mm/dk)", format_number(snap.feed_actual)),
        ("Programlanan F", format_number(snap.feed_programmed)),
        ("Devir S (dev/dk)", format_number(snap.spindle_actual)),
        ("Programlanan S", format_number(snap.spindle_programmed)),
        ("F override (%)", format_number(snap.feed_override)),
        ("S override (%)", format_number(snap.spindle_override)),
        ("Hızlı override (%)", format_number(snap.rapid_override)),
        ("Hızlı hareket (G0)", "—" if snap.is_rapid is None
                               else ("evet" if snap.is_rapid else "hayır")),
        ("Program", snap.program_name or "—"),
        ("Blok no", format_number(snap.block_number)),
        ("Takım no", format_number(snap.tool_number)),
    ]
    for ad, deger in alanlar:
        print(f"  {ad:<22}: {deger}")

    if snap.alarms:
        print("\n  Aktif alarmlar:")
        for alarm in snap.alarms:
            print(f"    [{alarm.code}] {alarm.text}")
    else:
        print("\n  Aktif alarm yok.")

    okunamayan = [ad for ad, deger in alanlar if deger == "—"]
    if okunamayan:
        print(
            "\n  NOT: Şu alanlar bu tezgahtan okunamıyor:\n    "
            + ", ".join(okunamayan)
        )
        print(
            "  Bu normaldir ve program yine çalışır — bu alanlar '—' olarak\n"
            "  kaydedilir. Gerçek F ve S değerleri LSV2 üzerinden hiçbir\n"
            "  tezgahta okunamaz; onun için OPC UA NC Server (Opsiyon 56) gerekir."
        )

    _surucu_detaylari(app)

    app.driver.disconnect()
    app.close_readonly()
    print("\nBağlantı kapatıldı. Tezgaha hiçbir şey yazılmadı.")
    return 0


def _surucu_detaylari(app) -> None:
    """Driver-specific diagnostics, printed after the generic snapshot."""
    driver = app.driver

    caps = getattr(driver, "capabilities", None)
    if isinstance(caps, dict) and caps:
        print("\n  LSV2 okuma yetenekleri (bu tezgahta):")
        etiketler = {
            "program_status": "Program durumu",
            "execution_state": "Çalışma modu",
            "override_state": "Override yüzdeleri",
            "error_messages": "Hata/alarm mesajları",
            "program_stack": "Program adı ve satır no",
            "tool": "Takım bilgisi",
        }
        for anahtar, calisiyor in caps.items():
            isaret = "evet" if calisiyor else "HAYIR"
            print(f"    {etiketler.get(anahtar, anahtar):<26}: {isaret}")
        if not all(caps.values()):
            print(
                "\n  'HAYIR' olanlar için DNC yazılım opsiyonu (Opsiyon 18)\n"
                "  gerekiyor olabilir. Tezgah servisine sorun."
            )

    if hasattr(driver, "browse_variables"):
        print("\n  OPC UA sunucusundaki okunabilir değişkenler taranıyor…")
        try:
            bulunanlar = driver.browse_variables()
        except DriverError as exc:
            print(f"    Tarama başarısız: {exc}")
            return
        if not bulunanlar:
            print("    Hiç okunabilir değişken bulunamadı.")
            return
        print(f"    {len(bulunanlar)} değişken bulundu. İlgili olabilecekler:\n")
        ilgili = [b for b in bulunanlar if b["tahmin"]]
        for satir in (ilgili or bulunanlar)[:40]:
            tahmin = f"  → node_{satir['tahmin']}" if satir["tahmin"] else ""
            print(f"    {satir['yol'][:44]:<44} {satir['node_id']:<22}"
                  f" = {satir['deger'][:20]}{tahmin}")
        print(
            "\n  Yukarıdaki node kimliklerini config.ini [opcua] bölümüne yazın,\n"
            "  örnek:  node_feed = ns=2;s=/Machine/Feed/Actual"
        )


def _normal_calistir(args) -> int:
    try:
        app = build_application(
            config_path=args.config,
            base_dir=args.dizin,
            driver_name=args.surucu,
            sim_speed=args.sim_hiz,
            sim_seed=args.sim_seed,
            port=args.port,
        )
    except AlreadyRunning as exc:
        print(f"\nHATA: {exc}\n", file=sys.stderr)
        return 1
    except DriverError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1

    cfg = app.cfg
    print("=" * 62)
    print(f" CNC Log System {__version__} — {cfg.machine_name} ({cfg.machine_id})")
    print("=" * 62)
    print(f" Sürücü     : {app.driver.describe()}")
    print(f" Ayarlar    : {cfg.source_path or 'varsayılan (config.ini bulunamadı)'}")
    print(f" Veri klasörü: {cfg.data_path}")
    print(f" Kayıt biçimi: {app.storage.backend}")
    print(f" Duruş eşiği : {cfg.idle_threshold_s:g} sn"
          + ("  (eşik kapalı — her duraklama kaydedilir)"
             if cfg.idle_threshold_s == 0 else ""))
    print(f" Örnekleme   : {cfg.sample_interval_s:g} sn")
    if app.recovered_events:
        print(f" Kurtarma    : önceki oturum düzgün kapanmamış, "
              f"{app.recovered_events} açık kayıt kapatıldı")

    app.start()

    web = None
    if not args.web_yok:
        from .web import WebServer

        web = WebServer(app)
        try:
            url, aciga_acik = web.start()
        except OSError as exc:
            print(f"\n HATA: Web arayüzü başlatılamadı ({exc}).", file=sys.stderr)
            print(f" Port {cfg.web_port} kullanımda olabilir; --port ile başka bir",
                  "port deneyin.", file=sys.stderr)
            app.stop()
            return 1

        print(f" Arayüz     : {url}")
        if aciga_acik:
            print(" UYARI      : Arayüz ağa açık (bind = "
                  f"{cfg.web_bind}). Sadece güvendiğiniz ağda kullanın.")
        if cfg.open_browser and not args.tarayici_yok:
            threading.Timer(0.8, _tarayici_ac, args=(url,)).start()

    print("=" * 62)
    print(" Kayıt başladı. Durdurmak için Ctrl+C.\n")

    durdur = threading.Event()

    def _kapat(signum, frame):  # noqa: ARG001
        durdur.set()

    signal.signal(signal.SIGINT, _kapat)
    try:
        signal.signal(signal.SIGTERM, _kapat)
    except (AttributeError, ValueError):
        pass  # Not available on every platform/thread.

    try:
        while not durdur.is_set():
            durdur.wait(0.5)
    except KeyboardInterrupt:
        pass

    print("\nKapatılıyor…")
    if web is not None:
        web.stop()
    app.stop()

    if not app.collector.stopped_cleanly:
        print("UYARI: kayıt işlemi zamanında durmadı; açık kayıtlar bir sonraki")
        print("       açılışta otomatik kapatılacak. Veri kaybı yok.")
    else:
        print("Açık kayıtlar kapatıldı. Veriler saklandı.")

    if app.collector.internal_errors:
        print(f"NOT: çalışma sırasında {app.collector.internal_errors} iç hata "
              "oluştu; ayrıntı log dosyasında.")
    return 0


def main(argv: Optional[list] = None) -> int:
    # Line-buffer stdout so its lines stay interleaved with stderr in the right
    # order when the output is piped or redirected to a file -- which is how it
    # runs as a service. UTF-8 because every message here is Turkish and the
    # Windows console defaults to a code page that mangles ş, ğ and ı.
    for stream, buffered in ((sys.stdout, True), (sys.stderr, False)):
        try:
            stream.reconfigure(  # type: ignore[attr-defined]
                encoding="utf-8", errors="replace", line_buffering=buffered
            )
        except (AttributeError, ValueError):
            pass  # Python < 3.7 or a stream that cannot be reconfigured.

    args = build_parser().parse_args(argv)

    if args.rapor is not None:
        return _rapor_calistir(args, args.rapor)
    if args.tara:
        return _tara(args)
    if args.test_baglanti:
        return _test_baglanti(args)
    return _normal_calistir(args)


if __name__ == "__main__":
    sys.exit(main())
