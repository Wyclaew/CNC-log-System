# -*- coding: utf-8 -*-
"""CNC Log System - her yerde calisan baslatici.

    python baslat.py              normal calistir
    python baslat.py --teshis     ortami incele, hicbir sey calistirma
    python baslat.py --tara       tezgahi agda ara
    python baslat.py --kisayol    masaustune/menuye cift tiklanir kisayol koy
    (butun parametreler programa aktarilir)

NEDEN BU DOSYA VAR
------------------
Heidenhain HEROS uzerinde 'sh baslat.sh' su hatayi verebiliyor:

    sh: no permission to execute baslat.sh

Sebebi genellikle dosya sisteminin 'noexec' baglanmis olmasi veya kabugun
kisitlanmasidir; 'chmod +x' bunu cozmez ve sudo sifresi de yoktur. Ama
'python baslat.py' calisir: burada calistirilan sey python yorumlayicisidir,
bu dosya sadece okunur. Calistirma bitine hic ihtiyac yoktur.

Bu dosya Python 2.6+ ve 3.x ile calisir; HEROS'ta sistem python'u 2.7'dir.
Asil program Python 3.7+ ister, bu yuzden burada once calisir bir Python 3
bulunur veya pakete gomulu tasinabilir surum acilir.

Not: f-string, dataclass gibi Python 3'e ozgu sozdizimi BU DOSYADA
kullanilmaz - yoksa Python 2.7 dosyayi okurken sozdizimi hatasi verir.
"""

from __future__ import print_function

import os
import platform
import subprocess
import sys
import tarfile

KLASOR = os.path.dirname(os.path.abspath(__file__))
GOMULU = os.path.join(KLASOR, "cnclog", "vendor", "python")

WINDOWS = os.name == "nt" or platform.system().lower().startswith("win")

#: Denenecek acma yerleri, sirasiyla. Ilk sirada program klasoru var (her sey
#: bir arada dursun); sonrasi noexec bir ev dizinine karsi yedeklerdir.
def _hedef_adaylari():
    adaylar = [os.path.join(GOMULU, "rt")]
    ev = os.path.expanduser("~")
    if ev and ev != "~":
        adaylar.append(os.path.join(ev, ".cnclog", "python"))
    for gecici in ("/tmp", "/var/tmp"):
        if os.path.isdir(gecici):
            adaylar.append(os.path.join(gecici, "cnclog-python"))
    if WINDOWS:
        temp = os.environ.get("TEMP") or os.environ.get("TMP")
        if temp:
            adaylar.append(os.path.join(temp, "cnclog-python"))
    return adaylar


def _arsiv_adi():
    if WINDOWS:
        return ["cpython-windows.tar.gz"]
    return ["cpython-linux-musl.tar.gz", "cpython-linux-gnu.tar.gz"]


def _python_yolu(kok):
    """Acilmis bir dagitimda yorumlayicinin yolu."""
    adaylar = [
        os.path.join(kok, "python", "bin", "python3"),
        os.path.join(kok, "python", "python.exe"),
    ]
    for yol in adaylar:
        if os.path.isfile(yol):
            return yol
    return None


def _calisir_mi(python_yolu):
    """Bu yorumlayici gercekten calisiyor ve gerekli moduller iceride mi?

    Sadece 'dosya var mi' bakmiyoruz: noexec bir dizinde dosya vardir ama
    calistirilamaz, ve bunu ancak deneyerek anlariz.
    """
    if not python_yolu or not os.path.isfile(python_yolu):
        return False
    kod = (
        "import sys\n"
        "if sys.version_info < (3, 7): raise SystemExit(1)\n"
        "import sqlite3, socket, json, threading, csv\n"
        "import configparser, http.server\n"
    )
    try:
        sonuc = subprocess.call(
            [python_yolu, "-c", kod],
            stdout=_DEVNULL(),
            stderr=_DEVNULL(),
        )
        return sonuc == 0
    except Exception:
        return False


_devnull_handle = [None]


def _DEVNULL():
    """subprocess.DEVNULL Python 2.7'de yok; acik bir dosya tutuyoruz."""
    if _devnull_handle[0] is None:
        _devnull_handle[0] = open(os.devnull, "wb")
    return _devnull_handle[0]


def _sistem_python3():
    """Sistemde kullanilabilir bir Python 3 var mi?"""
    adaylar = []
    # Bu betigi zaten bir python calistiriyor; once ona bakalim.
    if sys.version_info >= (3, 7):
        adaylar.append(sys.executable)
    isimler = ["python3", "python3.13", "python3.12", "python3.11",
               "python3.10", "python3.9", "python3.8", "python3.7", "python"]
    yollar = os.environ.get("PATH", "").split(os.pathsep)
    for isim in isimler:
        for dizin in yollar:
            if not dizin:
                continue
            tam = os.path.join(dizin, isim + (".exe" if WINDOWS else ""))
            if os.path.isfile(tam):
                adaylar.append(tam)
    gorulen = set()
    for aday in adaylar:
        if aday in gorulen:
            continue
        gorulen.add(aday)
        if _calisir_mi(aday):
            return aday
    return None


def _arsiv_ac(arsiv_yolu, hedef):
    """Arsivi hedefe acar. Basarisizsa False."""
    try:
        if not os.path.isdir(hedef):
            os.makedirs(hedef)
    except Exception:
        return False
    eski = os.path.join(hedef, "python")
    if os.path.isdir(eski):
        _sil(eski)
    try:
        arsiv = tarfile.open(arsiv_yolu, "r:gz")
        try:
            arsiv.extractall(hedef)
        finally:
            arsiv.close()
    except Exception:
        return False
    # tarfile calistirma bitlerini korur, ama bazi dosya sistemlerinde
    # kaybolabiliyor; garantiye alalim.
    yol = _python_yolu(hedef)
    if yol:
        try:
            os.chmod(yol, 0o755)
        except Exception:
            pass
    return True


def _sil(yol):
    try:
        import shutil

        shutil.rmtree(yol, ignore_errors=True)
    except Exception:
        pass


def python3_bul(sessiz=False):
    """Calisir bir Python 3 dondurur, yoksa None."""
    sistem = _sistem_python3()
    if sistem:
        if not sessiz:
            print("Sistem Python 3 bulundu: " + sistem)
        return sistem

    # Daha once acilmis bir kopya var mi?
    for hedef in _hedef_adaylari():
        yol = _python_yolu(hedef)
        if _calisir_mi(yol):
            if not sessiz:
                print("Gomulu Python kullaniliyor: " + yol)
            return yol

    # Arsivden ac. Her hedefi sirayla dene: ev dizini noexec olabilir,
    # o zaman /tmp genelde calisir.
    for arsiv_adi in _arsiv_adi():
        arsiv_yolu = os.path.join(GOMULU, arsiv_adi)
        if not os.path.isfile(arsiv_yolu):
            continue
        for hedef in _hedef_adaylari():
            print("Python 3 hazirlaniyor (%s -> %s)..."
                  % (arsiv_adi.replace("cpython-", "").replace(".tar.gz", ""),
                     hedef))
            if not _arsiv_ac(arsiv_yolu, hedef):
                continue
            yol = _python_yolu(hedef)
            if _calisir_mi(yol):
                print("Hazir: " + yol)
                return yol
            print("  bu konumda calistirilamadi, digeri deneniyor")
            _sil(os.path.join(hedef, "python"))
    return None


# --------------------------------------------------------------- teshis


def _komut(cmd):
    try:
        surec = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        cikti, _ = surec.communicate()
        if not isinstance(cikti, str):
            cikti = cikti.decode("utf-8", "replace")
        return cikti
    except Exception:
        return ""


def teshis():
    print("=" * 60)
    print("  CNC LOG SYSTEM - ORTAM TESHISI")
    print("=" * 60)
    print("  Platform      : %s %s" % (platform.system(), platform.release()))
    print("  Bu betigi calistiran python: %s" % sys.executable)
    print("  Surumu        : %d.%d.%d" % sys.version_info[:3])
    print("  Program klasoru: %s" % KLASOR)
    print("  Kullanici     : uid=%s" % (getattr(os, "getuid", lambda: "?")()))

    print("\n--- Dosya sistemi baglama secenekleri (noexec onemli) ---")
    mount = _komut(["mount"])
    if mount:
        for satir in mount.splitlines():
            if "noexec" in satir or " / " in satir or "/tmp" in satir \
                    or "home" in satir:
                print("    " + satir.strip()[:110])
    else:
        print("    (mount komutu calistirilamadi)")

    print("\n--- Acma yerleri: yazilabilir mi, calistirilabilir mi? ---")
    for hedef in _hedef_adaylari():
        durum = []
        ust = hedef
        while ust and not os.path.isdir(ust):
            yeni = os.path.dirname(ust)
            if yeni == ust:
                break
            ust = yeni
        durum.append("yazilabilir" if os.access(ust, os.W_OK) else "YAZILAMAZ")
        yol = _python_yolu(hedef)
        if yol:
            durum.append("acilmis kopya var")
            durum.append("CALISIYOR" if _calisir_mi(yol) else "CALISMIYOR")
        print("    %-46s %s" % (hedef, ", ".join(durum)))

    print("\n--- Gomulu arsivler ---")
    gerekli = _arsiv_adi()
    for ad in ("cpython-linux-musl.tar.gz", "cpython-linux-gnu.tar.gz",
               "cpython-windows.tar.gz"):
        yol = os.path.join(GOMULU, ad)
        isaret = "  <-- bu platform icin" if ad in gerekli else ""
        if os.path.isfile(yol):
            mb = os.path.getsize(yol) / (1024.0 * 1024.0)
            print("    %-30s VAR (%.0f MB)%s" % (ad, mb, isaret))
        else:
            print("    %-30s yok%s" % (ad, isaret))

    print("\n--- Sistemde Python surumleri ---")
    bulundu = False
    for isim in ("python", "python2", "python3"):
        for dizin in os.environ.get("PATH", "").split(os.pathsep):
            tam = os.path.join(dizin, isim + (".exe" if WINDOWS else ""))
            if os.path.isfile(tam):
                surum = _komut([tam, "--version"]).strip()
                if not surum:
                    surum = "(surum okunamadi)"
                print("    %-28s %s" % (tam, surum))
                bulundu = True
                break
    if not bulundu:
        print("    (PATH icinde python bulunamadi)")

    print("\n--- Ag adresleri ---")
    if WINDOWS:
        cikti = _komut(["ipconfig"])
        for satir in cikti.splitlines():
            if "IPv4" in satir:
                print("    " + satir.strip())
    else:
        cikti = _komut(["ip", "-4", "addr"]) or _komut(["ifconfig"])
        for satir in cikti.splitlines():
            if "inet " in satir:
                print("    " + satir.strip()[:90])
    if not cikti:
        print("    (ag komutu calistirilamadi)")

    print("\n" + "=" * 60)
    print("  SONUC")
    print("=" * 60)
    py3 = python3_bul(sessiz=True)
    if py3:
        print("  Calisir Python 3 VAR: %s" % py3)
        print("  Program calistirilabilir:  python baslat.py")
    else:
        print("  Calisir bir Python 3 BULUNAMADI.")
        print("  Yukaridaki 'acma yerleri' bolumunde hepsi YAZILAMAZ veya")
        print("  CALISMIYOR ise, dosya sistemi noexec baglanmis olabilir.")
        print("  Bu ciktinin tamamini gonderin.")
    print("=" * 60)
    return 0


# ----------------------------------------------------------------- main


KISAYOL_SABLONU = """[Desktop Entry]
Type=Application
Version=1.0
Name=CNC Log
GenericName=Tezgah Veri Kaydi
Comment=Tezgah verilerini kaydeder ve logu gosterir
Exec=%(exec)s
Path=%(klasor)s
Icon=utilities-system-monitor
Terminal=true
Categories=Utility;Monitor;
StartupNotify=true
"""


def kisayol_olustur():
    """Cift tiklanabilir bir kisayol yazar.

    Exec satiri 'python baslat.py' seklindedir: calistirilan sey python
    yorumlayicisidir, bu yuzden betik dosyasinin calistirma bitine ihtiyaci
    yoktur. noexec bagli bir dosya sisteminde de calisir.
    """
    py = sys.executable or "python"
    betik = os.path.join(KLASOR, "baslat.py")

    # .desktop Exec satirinda bosluk iceren yollar tirnak icinde olmali,
    # yoksa yol iki ayri argumana bolunur ve kisayol calismaz.
    def alintila(yol):
        if " " in yol or '"' in yol:
            return '"' + yol.replace('"', '\\"') + '"'
        return yol

    icerik = KISAYOL_SABLONU % {
        "exec": "%s %s" % (alintila(py), alintila(betik)),
        "klasor": KLASOR,
    }

    hedefler = []
    menu = os.path.join(os.path.expanduser("~"), ".local", "share",
                        "applications")
    hedefler.append(os.path.join(menu, "cnclog.desktop"))
    for ad in ("Desktop", "Masaüstü", "Masaustu"):
        masaustu = os.path.join(os.path.expanduser("~"), ad)
        if os.path.isdir(masaustu):
            hedefler.append(os.path.join(masaustu, "cnclog.desktop"))
            break

    yazilan = []
    for hedef in hedefler:
        try:
            dizin = os.path.dirname(hedef)
            if not os.path.isdir(dizin):
                os.makedirs(dizin)
            dosya = open(hedef, "w")
            try:
                dosya.write(icerik)
            finally:
                dosya.close()
            try:
                os.chmod(hedef, 0o755)  # XFCE masaustunde bu gerekebiliyor
            except Exception:
                pass
            yazilan.append(hedef)
        except Exception as hata:
            print("  yazilamadi: %s (%s)" % (hedef, hata))

    if not yazilan:
        print("Kisayol olusturulamadi.")
        return 1

    print("Kisayol olusturuldu:")
    for yol in yazilan:
        print("  " + yol)
    print("")
    print("Artik XFCE menusunde 'CNC Log' olarak gorunur ve masaustundeki")
    print("simgeye cift tiklayarak baslatabilirsiniz.")
    print("Menude hemen gorunmezse oturumu kapatip acin.")
    return 0


def main(argv):
    if "--teshis" in argv:
        return teshis()

    if "--kisayol" in argv:
        return kisayol_olustur()

    if "--python-bilgi" in argv:
        # Kisa surum: sadece hangi Python kullanilacagini soyler.
        py3 = python3_bul()
        if not py3:
            print("Calisir bir Python 3 bulunamadi. Ayrinti icin:")
            print("    python baslat.py --teshis")
            return 1
        print("Kullanilacak Python: " + py3)
        subprocess.call([py3, "--version"])
        print("Program klasoru    : " + KLASOR)
        return 0

    py3 = python3_bul()
    if not py3:
        print("", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(" HATA: Calisir bir Python 3 bulunamadi.", file=sys.stderr)
        print("", file=sys.stderr)
        print(" Ortami incelemek icin:", file=sys.stderr)
        print("     python baslat.py --teshis", file=sys.stderr)
        print(" Ciktinin tamamini gonderin.", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        return 1

    ortam = os.environ.copy()
    ortam["PYTHONIOENCODING"] = "utf-8"
    ortam["PYTHONUTF8"] = "1"

    komut = [py3, "-m", "cnclog"] + list(argv)
    try:
        return subprocess.call(komut, cwd=KLASOR, env=ortam)
    except KeyboardInterrupt:
        return 0
    except Exception as hata:
        print("Program baslatilamadi: %s" % hata, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
