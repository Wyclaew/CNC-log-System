/* Front end for the CNC log viewer.
   Polls the local API; never writes anything anywhere. All text coming from
   the API is inserted with textContent, never innerHTML. */

(function () {
  "use strict";

  var YENILEME_MS = 2000;
  var otomatik = true;
  var aktifSekme = "ana";
  var sunucuFarki = 0;      // browser clock - server clock, in seconds
  var sonDurum = null;
  var gunlerYuklendi = false;

  // ------------------------------------------------------------- yardimcilar

  function $(id) { return document.getElementById(id); }

  function sureMetni(saniye) {
    if (saniye === null || saniye === undefined || isNaN(saniye)) return "—";
    var t = Math.max(0, Math.round(saniye));
    if (t < 60) return t + "sn";
    if (t < 3600) return Math.floor(t / 60) + "dk " + pad(t % 60) + "sn";
    return Math.floor(t / 3600) + "sa " + pad(Math.floor((t % 3600) / 60)) + "dk";
  }

  function pad(n) { return n < 10 ? "0" + n : String(n); }

  function sayi(deger, basamak) {
    if (deger === null || deger === undefined) return "—";
    return basamak ? Number(deger).toFixed(basamak) : String(Math.round(deger));
  }

  function metin(el, deger, yokIse) {
    var bos = deger === null || deger === undefined || deger === "";
    el.textContent = bos ? (yokIse || "—") : deger;
    el.classList.toggle("yok", bos);
  }

  function saatBicim(tsSaniye) {
    var d = new Date(tsSaniye * 1000);
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  function getir(yol) {
    return fetch(yol, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  // ------------------------------------------------------------------ durum

  function durumUygula(d) {
    sonDurum = d;
    if (d.now) sunucuFarki = Date.now() / 1000 - d.now;

    $("makineAdi").textContent = d.machine_name || "CNC Log System";
    $("makineId").textContent = d.machine_id || "—";
    $("surucuAdi").textContent = d.driver || "—";
    $("depolama").textContent = "kayıt: " + (d.storage_backend || "?");
    $("altSurum").textContent = "sürüm " + (d.version || "?");

    // The big card shows the *raw* state: telling the operator the machine is
    // running while it visibly stands still would be worse than useless.
    var durum = d.raw_state || d.state;
    var kart = $("durumKart");
    kart.className = "durumKart s-" + durum;
    $("durumEtiket").textContent = d.raw_state_label || d.state_label || "—";

    var rozet = $("baglantiRozet");
    rozet.className = "baglanti " + (d.connected ? "acik" : "kopuk");
    $("baglantiYazi").textContent = d.connected ? "bağlı" : "bağlantı yok";

    if (d.pending_change) {
      $("durumNot").textContent =
        "kayda geçmesi için " + sayi(d.idle_threshold_s) + " sn eşiği bekleniyor";
    } else if (!d.connected && d.arama && d.arama.length) {
      // Auto driver still searching: show the last step, not just "no link".
      $("durumNot").textContent = d.arama[d.arama.length - 1];
    } else if (!d.connected && d.error) {
      // Multi-line driver errors would blow up the card; first line is enough.
      $("durumNot").textContent = String(d.error).split("\n")[0];
    } else {
      $("durumNot").textContent = "";
    }

    var bugun = d.bugun || {};
    $("mCalisma").textContent = sureMetni(bugun.run_s);
    $("mDurus").textContent = sureMetni(bugun.down_s);
    $("mKullanim").textContent =
      bugun.availability === null || bugun.availability === undefined
        ? "—"
        : "%" + Number(bugun.availability).toFixed(1);
    $("mDurusAdet").textContent = bugun.stop_count === undefined ? "—" : bugun.stop_count;
    $("mAlarmAdet").textContent = bugun.alarm_count === undefined ? "—" : bugun.alarm_count;

    metin($("oFeed"), d.feed_actual === null || d.feed_actual === undefined
      ? null : sayi(d.feed_actual));
    metin($("oSpindle"), d.spindle_actual === null || d.spindle_actual === undefined
      ? null : sayi(d.spindle_actual));
    metin($("oFeedOv"), d.feed_override === null || d.feed_override === undefined
      ? null : sayi(d.feed_override));
    metin($("oSpindleOv"), d.spindle_override === null || d.spindle_override === undefined
      ? null : sayi(d.spindle_override));

    var hizli = $("oRapid");
    if (d.is_rapid === null || d.is_rapid === undefined) {
      metin(hizli, null);
      hizli.classList.remove("hizli");
    } else {
      hizli.textContent = d.is_rapid ? "EVET" : "hayır";
      hizli.classList.remove("yok");
      hizli.classList.toggle("hizli", !!d.is_rapid);
    }

    metin($("oProgram"), d.program_name);
    metin($("oBlok"), d.block_number);
    metin($("oTakim"), d.tool_number);

    var alarmlar = d.alarms || [];
    var kutu = $("alarmKutu");
    var liste = $("alarmListe");
    liste.textContent = "";
    if (alarmlar.length) {
      kutu.classList.remove("gizli");
      alarmlar.forEach(function (a) {
        var li = document.createElement("li");
        li.textContent = "[" + a.code + "] " + a.text;
        liste.appendChild(li);
      });
    } else {
      kutu.classList.add("gizli");
    }

    $("altDurum").textContent =
      "örnekleme " + sayi(d.sample_interval_s, 1) + " sn · duruş eşiği " +
      sayi(d.idle_threshold_s) + " sn · " + (d.ticks || 0) + " ölçüm";

    sureTazele();
  }

  function sureTazele() {
    if (!sonDurum || !sonDurum.state_since) {
      $("durumSure").textContent = "—";
      return;
    }
    var simdi = Date.now() / 1000 - sunucuFarki;
    $("durumSure").textContent = sureMetni(simdi - sonDurum.state_since) + " önce başladı";
  }

  // ------------------------------------------------------------------ loglar

  function loglariCiz(satirlar) {
    var kutu = $("logKutu");
    var kaydirma = kutu.scrollTop;
    kutu.textContent = "";

    if (!satirlar.length) {
      var bos = document.createElement("div");
      bos.className = "bosluk";
      bos.textContent = "Bu filtrede kayıt yok.";
      kutu.appendChild(bos);
      return;
    }

    var parca = document.createDocumentFragment();
    satirlar.forEach(function (s) {
      var satir = document.createElement("div");
      satir.className = "satir t-" + (s.tur || "olcum") +
        (s.durum ? " d-" + s.durum : "");

      var saat = document.createElement("span");
      saat.className = "sSaat";
      saat.textContent = s.saat;

      var etiket = document.createElement("span");
      etiket.className = "sEtiket";
      etiket.textContent = s.etiket;

      var mesaj = document.createElement("span");
      mesaj.className = "sMesaj";
      mesaj.textContent = s.mesaj;

      satir.appendChild(saat);
      satir.appendChild(etiket);
      satir.appendChild(mesaj);
      parca.appendChild(satir);
    });
    kutu.appendChild(parca);
    kutu.scrollTop = kaydirma;
  }

  function loglariYenile() {
    var tarih = $("fTarih").value || "bugun";
    var tur = $("fTur").value || "hepsi";
    return getir("/api/loglar?kaynak=metin&limit=400&tarih=" +
                 encodeURIComponent(tarih) + "&tip=" + encodeURIComponent(tur))
      .then(function (d) { loglariCiz(d.satirlar || []); })
      .catch(function () { /* transient; the next poll retries */ });
  }

  // ------------------------------------------------------------------ rapor

  function ozetSatiri(baslik, deger) {
    var tr = document.createElement("tr");
    var th = document.createElement("th");
    th.textContent = baslik;
    var td = document.createElement("td");
    td.className = "sayi";
    td.textContent = deger;
    tr.appendChild(th);
    tr.appendChild(td);
    return tr;
  }

  function tabloYap(basliklar, satirlar, sayiSutunlari) {
    var sar = document.createElement("div");
    sar.className = "tabloSar";
    var table = document.createElement("table");

    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    basliklar.forEach(function (b, i) {
      var th = document.createElement("th");
      if (sayiSutunlari.indexOf(i) >= 0) th.className = "sayi";
      th.textContent = b;
      trh.appendChild(th);
    });
    thead.appendChild(trh);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    satirlar.forEach(function (satir) {
      var tr = document.createElement("tr");
      satir.forEach(function (hucre, i) {
        var td = document.createElement("td");
        if (sayiSutunlari.indexOf(i) >= 0) td.className = "sayi";
        else if (i === satir.length - 1) td.className = "genis";
        td.textContent = hucre === null || hucre === undefined ? "—" : String(hucre);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    sar.appendChild(table);
    return sar;
  }

  function blokYap(baslik, icerik) {
    var blok = document.createElement("section");
    blok.className = "raporBlok";
    var h3 = document.createElement("h3");
    h3.textContent = baslik;
    blok.appendChild(h3);
    blok.appendChild(icerik);
    return blok;
  }

  function raporCiz(r) {
    var hedef = $("raporIcerik");
    hedef.textContent = "";

    var ozet = document.createElement("table");
    var tbody = document.createElement("tbody");
    tbody.appendChild(ozetSatiri("Çalışma süresi", sureMetni(r.run_s)));
    tbody.appendChild(ozetSatiri("Duruş süresi", sureMetni(r.down_s)));
    tbody.appendChild(ozetSatiri("Kurulum / elle kumanda", sureMetni(r.setup_s)));
    tbody.appendChild(ozetSatiri("Ölçülemeyen (bağlantı yok)", sureMetni(r.offline_s)));
    if (r.rapid_s !== null && r.rapid_s !== undefined) {
      tbody.appendChild(ozetSatiri("Hızlı hareket (G0)", sureMetni(r.rapid_s)));
    }
    tbody.appendChild(ozetSatiri(
      "Kullanılabilirlik",
      r.availability === null || r.availability === undefined
        ? "—" : "%" + Number(r.availability).toFixed(1)
    ));
    tbody.appendChild(ozetSatiri("Duruş sayısı", r.stop_count));
    tbody.appendChild(ozetSatiri("Alarm sayısı", r.alarm_count));
    ozet.appendChild(tbody);
    hedef.appendChild(blokYap("Gün özeti", ozet));

    if (r.vardiyalar && r.vardiyalar.length) {
      hedef.appendChild(blokYap("Vardiyalar", tabloYap(
        ["Vardiya", "Çalışma", "Duruş", "Kullanılabilirlik", "Duruş sayısı", "Alarm"],
        r.vardiyalar.map(function (v) {
          return [
            v.etiket,
            sureMetni(v.run_s),
            sureMetni(v.down_s),
            v.availability === null || v.availability === undefined
              ? "—" : "%" + Number(v.availability).toFixed(1),
            v.stop_count,
            v.alarm_count
          ];
        }),
        [1, 2, 3, 4, 5]
      )));
    }

    if (r.programs && r.programs.length) {
      hedef.appendChild(blokYap("Program bazlı süreler", tabloYap(
        ["Program", "Çalışma", "Yüklü kalma", "Kez"],
        r.programs.map(function (p) {
          return [p.program_name, sureMetni(p.run_s), sureMetni(p.loaded_s), p.count];
        }),
        [1, 2, 3]
      )));
    }

    if (r.stops && r.stops.length) {
      hedef.appendChild(blokYap("Duruşlar (uzundan kısaya)", tabloYap(
        ["Başlangıç", "Süre", "Durum", "Sebep"],
        r.stops.slice(0, 50).map(function (s) {
          return [
            saatBicim(s.ts_start),
            sureMetni(s.duration_s),
            s.state_label,
            (s.reason || "—") + (s.open ? "  (devam ediyor)" : "")
          ];
        }),
        [1]
      )));
    }

    if (r.alarms && r.alarms.length) {
      hedef.appendChild(blokYap("Alarmlar", tabloYap(
        ["Başlangıç", "Süre", "Kod", "Mesaj"],
        r.alarms.map(function (a) {
          return [
            saatBicim(a.ts_start),
            sureMetni(a.duration_s),
            a.code,
            (a.text || "—") + (a.open ? "  (aktif)" : "")
          ];
        }),
        [1]
      )));
    }

    if (!r.programs.length && !r.stops.length && !r.alarms.length && !r.run_s) {
      var bos = document.createElement("div");
      bos.className = "bosluk";
      bos.textContent = "Bu tarihte kayıt yok.";
      hedef.appendChild(bos);
    }
  }

  function raporYenile() {
    var tarih = $("rTarih").value || "bugun";
    $("rCsv").href = "/api/rapor.csv?tarih=" + encodeURIComponent(tarih);
    return getir("/api/rapor?tarih=" + encodeURIComponent(tarih))
      .then(raporCiz)
      .catch(function (e) {
        $("raporIcerik").textContent = "Rapor alınamadı: " + e.message;
      });
  }

  // ------------------------------------------------------------------ gunler

  function gunleriYukle() {
    return getir("/api/gunler").then(function (d) {
      var gunler = d.gunler || [];
      [["fTarih", true], ["rTarih", false]].forEach(function (pair) {
        var sec = $(pair[0]);
        var oncekiDeger = sec.value;
        sec.textContent = "";
        var bugun = document.createElement("option");
        bugun.value = "bugun";
        bugun.textContent = "Bugün";
        sec.appendChild(bugun);
        gunler.forEach(function (g) {
          var o = document.createElement("option");
          o.value = g;
          o.textContent = g;
          sec.appendChild(o);
        });
        if (oncekiDeger) sec.value = oncekiDeger;
      });
      gunlerYuklendi = true;
    }).catch(function () { /* ignore */ });
  }

  // ----------------------------------------------------------------- sekme

  function sekmeAc(ad) {
    aktifSekme = ad;
    document.querySelectorAll(".sekme").forEach(function (b) {
      var secili = b.dataset.sekme === ad;
      b.classList.toggle("aktif", secili);
      b.setAttribute("aria-selected", secili ? "true" : "false");
    });
    $("sekme-ana").classList.toggle("gizli", ad !== "ana");
    $("sekme-rapor").classList.toggle("gizli", ad !== "rapor");
    if (ad === "rapor") raporYenile();
  }

  // ------------------------------------------------------------------ dongu

  function dongu() {
    getir("/api/durum").then(durumUygula).catch(function () {
      var rozet = $("baglantiRozet");
      rozet.className = "baglanti kopuk";
      $("baglantiYazi").textContent = "program yanıt vermiyor";
    });
    if (aktifSekme === "ana" && otomatik) loglariYenile();
    if (!gunlerYuklendi) gunleriYukle();
  }

  // -------------------------------------------------------------- baslangic

  document.querySelectorAll(".sekme").forEach(function (b) {
    b.addEventListener("click", function () { sekmeAc(b.dataset.sekme); });
  });
  $("fTarih").addEventListener("change", loglariYenile);
  $("fTur").addEventListener("change", loglariYenile);
  $("fOtomatik").addEventListener("change", function (e) {
    otomatik = e.target.checked;
    if (otomatik) loglariYenile();
  });
  $("rTarih").addEventListener("change", raporYenile);

  gunleriYukle().then(loglariYenile);
  dongu();
  setInterval(dongu, YENILEME_MS);
  setInterval(sureTazele, 1000);
})();
