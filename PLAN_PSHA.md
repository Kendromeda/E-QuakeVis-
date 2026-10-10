# Plan: Peta PSHA (Probabilistic Seismic Hazard Analysis) di E-QuakeVis

## Goal
Menghasilkan peta bahaya gempa probabilistik untuk Indonesia, yaitu nilai PGA dan
spektral (SA 0,2 s dan 1,0 s) untuk peluang terlampaui 10% dan 2% dalam 50 tahun
(periode ulang 475 dan 2475 tahun) pada kondisi batuan dasar (Vs30 = 760 m/s).
Peta ditampilkan, ditelusuri, dan diekspor dari aplikasi E-QuakeVis.

## Status (2026-10-06)
- **Step 1 selesai.** `.venv-oq` (Python 3.12) dengan OpenQuake 3.26.2 terpasang
  dari daftar wheel resmi GEM (GDAL tidak punya wheel Windows di PyPI). Demo QA
  berjalan. Datastore diarahkan ke `D:\oqdata` lewat `OQ_DATADIR`.
- **Step 2 selesai.** Model GEM IDN v2017.1.0 diunduh dan diverifikasi SHA-256.
  Inventaris berisi 13 megathrust, 251 sesar, 2 lapis grid dangkal, dan 4 lapis
  grid intraslab. Lihat `data/README.md`.
- **Step 3:**
  - Run uji `jabar_0p1` selesai: 382 situs dalam 20,6 menit.
  - `ps_grid_spacing = 50` ditolak karena nilainya bias −4 s/d −7%.
  - Run seluruh Indonesia tidak dibutuhkan karena pengguna hanya memakai
    sebagian wilayah. Run nasional 0,2° dihentikan; run dibuat per provinsi
    dengan `--provinces`.
  - Pembandingan dengan RSA Cipta Karya selesai untuk 48 situs. PGA 2475
    tahun ÷ PGA SNI bermedian 1,17 (P10–P90 1,06–1,22). Lihat
    `data/README.md`.
- **Step 4 selesai.** Tab "Bahaya Gempa (PSHA)" menyediakan:
  - peta raster terklasifikasi;
  - pemilih run, IMT, dan periode ulang;
  - klik peta untuk melihat kurva hazard;
  - unduhan CSV.

  Ekspor ke Peta Publikasi juga sudah ada: toggle "Layer bahaya gempa (PSHA)"
  menambahkan raster, legenda, dan sumber model ke PNG/PDF.

## Context
Aplikasi sudah memiliki katalog BMKG (2008–2026) dan USGS reviewed M≥4, sesar
dangkal PuSGeN 2024 (401 segmen), grid Slab2 (sum, sul, hal, png, phi), garis
palung, dan batas wilayah. Aplikasi belum punya model sumber megathrust/intraslab,
kedalaman seismogenik sesar, katalog Mw yang homogen dan dideklasterisasi, GMPE,
maupun mesin perhitungan hazard.

Perhitungan hazard memakai **OpenQuake engine** (GEM). Mesin ini tidak ditulis
ulang di aplikasi karena GMPE dan geometri rupture pada slab melengkung sulit
diimplementasikan dengan benar. Ada dua batasan teknis:

- OpenQuake engine 3.26 membutuhkan Python ≥ 3.11, sedangkan aplikasi berjalan di
  Python 3.9.
- OpenQuake berlisensi AGPL-3.0, sedangkan aplikasi berlisensi MIT dan dibundel
  sebagai desktop app (Nuitka).

Karena itu OpenQuake dijalankan sebagai **proses terpisah di lingkungan Python
sendiri**, dan aplikasi hanya membaca keluaran CSV-nya.

Pengerjaan dibagi tiga fase. Fase 1 menghasilkan peta PSHA yang bisa dipakai secepat
mungkin dari model resmi yang sudah jadi. Fase 2 menggantinya dengan model sumber
yang dibangun dari data aplikasi sendiri (PuSGeN 2024 + katalog terbaru). Fase 3
bersifat opsional.

## Process Overview

**Fase 1 — Baseline dari model GEM Indonesia (Irsyam dkk. 2017)**
1. Siapkan lingkungan OpenQuake terpisah.
2. Unduh dan inventarisasi model GEM IDN v2017.1.0.
3. Jalankan perhitungan baseline (area uji, lalu seluruh Indonesia) dan validasi.
4. Tampilkan hasil PSHA di aplikasi (tab baru, layer peta, kurva hazard, ekspor).

**Fase 2 — Model sumber dari data aplikasi**
5. Bangun katalog siap-PSHA (gabung, Mw, deklasterisasi, kelengkapan, klasifikasi tektonik).
6. Konversi sesar dangkal PuSGeN 2024 menjadi sumber OpenQuake.
7. Bangun sumber megathrust (segmen PuSGeN + geometri Slab2).
8. Bangun sumber intraslab dan kegempaan latar (gridded seismicity).
9. Susun logic tree GMPE dan generator `job.ini`.
10. Jalankan, bandingkan dengan baseline Fase 1 dan peta PuSGeN 2024.

**Fase 3 — Lanjutan (opsional)**
11. Peta di permukaan tanah (Vs30), disagregasi, kurva hazard per titik, dan risiko.

## Detailed Steps

### Step 1: Lingkungan OpenQuake terpisah
**What happens:** Membuat virtual environment kedua (`.venv-oq`, Python 3.11 atau
3.12), memasang `openquake.engine`, lalu menjalankan satu demo bawaan OpenQuake
untuk memastikan mesin berjalan di Windows.
**Input:** Python 3.11/3.12 terpasang di samping Python 3.9 aplikasi.
**Output:** Perintah `oq engine --run job.ini` berjalan. Versi OpenQuake dicatat.
**Decisions:**
- OpenQuake **tidak** dimasukkan ke `requirements.txt` aplikasi dan **tidak**
  dibundel ke `desktop_nuitka/`, untuk menjaga batas lisensi AGPL dan ukuran build.
- Skrip pemicu: `scripts/run_psha.py` (dijalankan dengan `.venv-oq`). Skrip ini
  membuat folder run, menyalin `job.ini`, menjalankan engine, dan mengekspor hasil.
**Notes:** Tambahkan `.venv-oq/` dan folder keluaran mentah OpenQuake ke `.gitignore`.

### Step 2: Unduh dan inventarisasi model GEM IDN
**What happens:** Mengunduh model input OpenQuake Indonesia v2017.1.0 dari GEM
(lisensi CC BY-SA 4.0) ke `data/psha/gem_idn_v2017.1.0/`. Lalu membuat skrip
inventaris yang membaca XML sumber dan mencatat untuk setiap sumber: tipe sumber,
tatanan tektonik, Mmax, MFD, `upperSeismoDepth`/`lowerSeismoDepth`, serta dip/rake.
**Input:** Tautan unduhan dari halaman produk GEM.
**Output:**
- Folder model + `manifest.json` (URL, SHA-256, tanggal unduh, lisensi, versi),
  mengikuti konvensi `data/slab2/*.manifest.json`.
- `data/psha/gem_idn_inventory.csv`, ditambah ringkasan jumlah segmen megathrust,
  zona intraslab, sesar, dan point source.
- Bagian baru di `data/README.md` dengan atribusi Irsyam dkk. (2017) dan GEM.
**Decisions:** Awalnya v2017.1.0. Pada 2026-10-08 pengguna memilih **v2024.0.0**
(CC BY-NC-SA 4.0, non-komersial) sebagai model default; v2017.1.0 tetap tersedia
lewat `--model v2017.1.0` dan hasil `jabar_0p1` tetap v2017.
**Notes:** Inventaris ini juga menjadi sumber kedalaman seismogenik dan parameter
pembanding untuk Fase 2.

### Step 3: Perhitungan baseline dan validasi
**What happens:** Menjalankan perhitungan klasik (`calculation_mode = classical`)
dalam dua tahap:
1. **Area uji kecil** (misalnya Jawa Barat): grid 0,1°, PGA, 10%/50 tahun. Ini
   untuk mengukur waktu hitung dan memori.
2. **Seluruh Indonesia** dengan IMT dan periode ulang lengkap.
**Input:** Model GEM IDN, `job.ini` hasil generator.
**Output:** `data/psha/results/<run_id>/`, berisi `hazard_map-mean.csv`,
`hazard_curve-mean-*.csv`, `job.ini`, dan `manifest.json` (versi model, versi
OpenQuake, parameter grid, waktu hitung).
**Decisions:**
- Situs perhitungan hanya titik di daratan, yang dihasilkan dari
  `provinsi_indonesia_38.geojson`. Mulai dari spasi 0,1° (sekitar 11 km), lalu
  turunkan ke sekitar 0,05° bila waktu hitung memungkinkan. GEM sendiri memakai 6 km.
- Vs30 = 760 m/s, `truncation_level = 5` (sama dengan GEM), `investigation_time = 50`.
**Notes (validasi):** Bandingkan nilai PGA 10%/50 tahun di titik kontrol (Jakarta,
Padang, Bandung, Yogyakarta, Palu, Ambon) dengan GEM Global Seismic Hazard Map dan
peta PuSGeN 2017. Karena modelnya sama, selisih terhadap GEM harus kecil dan hanya
berasal dari grid/versi engine.

### Step 4: Tampilan PSHA di aplikasi
**What happens:** Modul baru `utils/psha_results.py` memuat dan memvalidasi hasil
run, lalu mengubah CSV menjadi grid. Tab baru **Bahaya Gempa (PSHA)** di `app.py`
menampilkan:
- peta kontur/raster nilai hazard di atas peta Folium yang sudah ada;
- pilihan IMT (PGA, SA 0,2 s, SA 1,0 s) dan periode ulang (475, 2475 tahun);
- legenda dengan satuan g dan keterangan run (model, versi, kondisi situs);
- klik titik untuk menampilkan kurva hazard (Plotly) di titik grid terdekat;
- ekspor CSV grid dan peta publikasi (lewat `utils/publication_map.py`);
- overlay layer yang sudah ada (sesar, palung, kontur slab, batas wilayah).
**Input:** Folder `data/psha/results/<run_id>/`.
**Output:** Peta PSHA interaktif di aplikasi, tanpa OpenQuake terpasang di runtime.
**Decisions:**
- Aplikasi membaca hasil yang sudah selesai. Sejak 2026-10-07 aplikasi juga
  dapat **meluncurkan** `scripts/run_psha.py` dari formulir "Hitung PSHA wilayah
  baru" (provinsi + jarak grid 0,1/0,2/0,25/0,5°) bila `.venv-oq` ada. Run
  berjalan sebagai proses latar terpisah (`utils/psha_jobs.py`), bukan di dalam
  Streamlit, sehingga run berjam-jam tetap aman. Hanya satu run sekaligus.
- Bila hasil tidak ada atau rusak, munculkan error yang jelas
  (`PSHAResultError`), mengikuti pola `SlabGridError`. Tidak ada peta sintetis.
**Notes:** Uji: `tests/test_psha_results.py` (parsing, validasi kolom, interpolasi
titik) dan e2e Playwright untuk tab baru.

**Fase 1 selesai di sini: aplikasi sudah bisa menampilkan peta PSHA.**

### Step 5: Katalog siap-PSHA
**What happens:** Skrip `scripts/build_psha_catalog.py` melakukan langkah berikut:
1. **Gabung dan dedup** BMKG + USGS reviewed, ditambah **ISC-GEM** (perlu diunduh)
   untuk kejadian sebelum 2008. Event dianggap sama bila selisih waktu dan jarak
   episenter di bawah ambang tertentu (misalnya 16 detik dan 100 km). Prioritas
   magnitudo: Mw langsung > Mw hasil konversi.
2. **Homogenisasi Mw** memakai relasi konversi mb/MLv/Ms → Mw dari buku PuSGeN
   2017/2024. Relasi dan sumbernya dicatat di kode.
3. **Klasifikasi tektonik** setiap event memakai grid Slab2 yang sudah ada:
   - *interface*: dekat permukaan slab dan di atas batas kedalaman seismogenik;
   - *intraslab*: di dalam lempeng yang menunjam, di bawah interface;
   - *crustal*: dangkal di lempeng atas.
4. **Deklasterisasi** Gardner–Knopoff (tersedia di toolkit OpenQuake hmtk).
5. **Kelengkapan** (Stepp 1972) per tatanan tektonik untuk mendapatkan Mc per periode.
**Input:** Tiga katalog, grid Slab2, relasi konversi magnitudo.
**Output:** `data/psha/catalog_mw_declustered.csv` dengan kolom `tectonic_class`,
`mw`, `mw_source`, `mainshock`, serta tabel kelengkapan.
**Decisions:** Klasifikasi slab memakai `utils/slab_grid.py` yang sudah ada, agar
tidak membuat geometri slab baru.
**Notes:** Uji: dedup, konversi Mw, klasifikasi pada event sintetis di atas/bawah
slab, dan jumlah mainshock deklasterisasi yang stabil.

### Step 6: Sesar dangkal PuSGeN 2024 → Simple Fault Source
**What happens:** Skrip `scripts/build_psha_crustal_faults.py` mengubah 401 segmen
`sesar_pusgen_2024.geojson` menjadi `SimpleFaultSource` OpenQuake:
- Parse `Type`, misalnya `R45S` menjadi naik, dip 45°, arah dip ke selatan, rake 90;
  `SS-LL90` menjadi geser mengiri, dip 90°, rake 0; `N60E` menjadi turun, rake −90.
- Arah trace disesuaikan dengan aturan tangan kanan OpenQuake, sehingga bidang
  miring ke arah dip yang tertulis.
- Slip rate di-parse (desimal koma, rentang diambil nilai tengah dan dicatat).
- Kedalaman seismogenik atas/bawah diambil dari inventaris GEM (Step 2) untuk sesar
  yang cocok. Untuk sesar baru/berbeda, gunakan kedalaman D90 kegempaan crustal
  regional (Step 5).
- MFD: laju momen dihitung dari slip rate (M₀ = μ·A·s, μ = 3×10¹⁰ Pa). Model
  karakteristik dan Gutenberg–Richter dijadikan cabang logic tree.
**Input:** Sesar PuSGeN 2024, inventaris GEM, katalog Step 5.
**Output:** `data/psha/model/crustal_faults.xml` dan laporan konversi per segmen.
**Decisions:** Tujuh baris tidak standar ditangani lewat tabel koreksi eksplisit
yang terdokumentasi, bukan tebakan otomatis. Barisnya: `SS` tanpa dip,
`R13&30NW`, `R16&30N`, `RR45S`, dan slip rate `0,1-0,5`.
**Notes:** Uji: parser `Type`, arah dip vs urutan trace, dan konservasi laju momen.

### Step 7: Sumber megathrust
**What happens:** Mendigitasi tabel segmen megathrust dari buku PuSGeN 2024
(nama segmen, batas, Mmax, slip rate, lebar, kedalaman seismogenik) ke
`data/psha/megathrust_segments_pusgen2024.csv`, dengan nomor halaman sumber per
baris. Geometri bidang diambil dari grid Slab2 yang dipotong ke rentang kedalaman
seismogenik dan batas segmen, lalu dijadikan `ComplexFaultSource` (atau
`KiteFaultSource`). MFD berasal dari slip rate dan Mmax, diperiksa terhadap laju
kegempaan interface dari katalog Step 5.
**Input:** Buku PuSGeN 2024 (PDF), grid Slab2, palung Slab2, katalog interface.
**Output:** `data/psha/model/megathrust.xml`, ditambah layer peta segmen megathrust
di aplikasi.
**Decisions:** Bila tabel PuSGeN 2024 ternyata tidak lengkap, gunakan parameter
megathrust dari model GEM (Step 2) sebagai fallback yang tercatat sumbernya.
**Notes:** Buku PuSGeN 2024 belum diperiksa isinya. Verifikasi dulu bahwa tabel
segmen dan parameternya memang ada.

### Step 8: Sumber intraslab dan kegempaan latar
**What happens:**
- **Intraslab:** event intraslab hasil deklasterisasi dibagi per rentang kedalaman
  (mengikuti pembagian PuSGeN, misalnya 50–100, 100–200, 200–300 km). Nilai a dan b
  dihitung per zona (maximum likelihood Weichert), lalu dibuat *smoothed gridded
  seismicity* (Frankel 1995) sebagai point source pada kedalaman tengah tiap rentang.
- **Background crustal:** cara yang sama untuk event crustal dangkal yang tidak
  dijelaskan oleh sesar.
**Input:** Katalog Step 5, Mmax per zona (PuSGeN/GEM).
**Output:** `data/psha/model/intraslab_*.xml`, `background_crustal.xml`, dan tabel
a/b/Mmax per zona.
**Decisions:** Untuk mencegah laju gempa terhitung ganda, event di dalam buffer
sesar (sejalan dengan `jarak_sesar_km` yang sudah dihitung aplikasi) dan di zona
interface dikeluarkan dari background crustal.
**Notes:** Uji: estimasi b-value pada katalog sintetis dengan b yang diketahui.

### Step 9: Logic tree GMPE dan generator job.ini
**What happens:** Membuat `gmmLT.xml` dengan cabang awal yang mengikuti GEM IDN:
- Interface: Abrahamson 2015, Parker 2020, Zhao 2006, Kuehn 2020 (bobot masing-masing 0,25).
- Crustal: BSSA14, CB14, CY14, Cauzzi 2014, Zhao 2006 Asc (bobot masing-masing 0,2).
- Intraslab: Abrahamson 2015, Parker 2020, Zhao 2006, Kuehn 2020 (bobot masing-masing 0,25).

`scripts/build_psha_job.py` membuat `job.ini` dari konfigurasi (wilayah, spasi grid,
IMT, periode ulang, Vs30).
**Output:** `data/psha/model/gmmLT.xml`, `ssmLT.xml`, `job.ini`.

### Step 10: Run model sendiri dan perbandingan
**What happens:** Menjalankan model Fase 2, lalu membandingkannya dengan baseline
Fase 1 dan peta PuSGeN 2024: peta selisih dan rasio, nilai di titik kontrol, serta
kontribusi per tipe sumber. Aplikasi bisa memilih run mana yang ditampilkan (Step 4
sudah berbasis `run_id`).
**Output:** Run kedua di `data/psha/results/`, dokumen perbandingan, dan selektor
model di tab PSHA.

### Step 11 (opsional): Lanjutan
- Peta permukaan: Vs30 global berbasis topografi (Allen & Wald 2009) sebagai
  `site_model.csv`.
- Disagregasi M–R–ε untuk kota terpilih.
- Kurva hazard on-demand per titik lewat `openquake.hazardlib` di proses terpisah.
- Risiko awal: hazard dikombinasikan dengan kepadatan penduduk kecamatan yang
  sudah ada.

## Edge Cases and Failure Modes
- **Run nasional terlalu berat:** mulai dari area uji dan grid 0,1°. Gunakan
  `pointsource_distance`/`ps_grid_spacing` dan batasi `maximum_distance` per
  tatanan tektonik.
- **Arah dip terbalik** pada sesar: dicegah dengan uji orientasi trace di Step 6.
- **Laju gempa terhitung ganda** antara sesar, megathrust, dan background: dicegah
  dengan buffer dan klasifikasi tektonik (Step 5 dan 8).
- **Konversi Mw** memperbesar ketidakpastian, terutama untuk `M` tanpa tipe di
  katalog BMKG. Event bertipe `M` tanpa relasi yang sah dikeluarkan dari perhitungan
  a/b, dan jumlahnya dilaporkan.
- **Katalog pendek** (BMKG mulai 2008): laju M besar mengandalkan ISC-GEM dan slip
  rate, bukan katalog BMKG saja.
- **Lisensi:** turunan dari GEM v2017.1.0 harus berlisensi CC BY-SA 4.0 dengan
  atribusi. v2024.0.0 hanya untuk non-komersial. OpenQuake (AGPL-3.0) tidak
  dibundel ke aplikasi.
- **Hasil run hilang atau rusak:** error eksplisit, tanpa fallback sintetis.

## Dependencies and Requirements
- Python 3.11/3.12 + `openquake.engine` 3.26.x di `.venv-oq` (terpisah dari aplikasi).
- Model GEM IDN v2024.0.0 (default, CC BY-NC-SA 4.0) dan v2017.1.0 (CC BY-SA 4.0).
- Buku Peta Sumber dan Bahaya Gempa Indonesia 2024 (PDF, Cipta Karya PUPR) untuk
  Step 7 dan relasi Mw.
- Katalog ISC-GEM (unduh) untuk kejadian sebelum 2008.
- Data yang sudah ada: katalog BMKG/USGS, sesar PuSGeN 2024, grid Slab2, palung,
  batas provinsi.

## Open Questions
1. ~~**Tujuan pemakaian**~~: pengguna memilih GEM v2024.0.0 (2026-10-08), sehingga
   hasil PSHA dari model ini hanya untuk penggunaan non-komersial.
2. **Cakupan wilayah tahap awal:** seluruh Indonesia, atau Jawa–Sumatra dulu (sejalan
   dengan cakupan data kepadatan penduduk)?
3. **Keluaran yang dibutuhkan:** cukup PGA 475/2475 tahun, atau juga SA 0,2 s dan
   1,0 s seperti peta SNI 1726?
4. **Fase 2 perlu atau tidak:** apakah Fase 1 (model resmi 2017) sudah cukup untuk
   kebutuhan Anda?

## Success Criteria
- **Fase 1:** tab PSHA menampilkan peta PGA 10%/50 tahun seluruh Indonesia. Nilai
  di titik kontrol konsisten dengan GEM Global Seismic Hazard Map, dan sisa
  selisihnya bisa dijelaskan oleh spasi grid/versi engine. Kurva hazard dan ekspor
  berfungsi. `pytest` dan uji JS lulus.
- **Fase 2:** model dari data aplikasi berjalan end-to-end dari skrip yang bisa
  direproduksi. Selisihnya terhadap baseline dan PuSGeN 2024 terdokumentasi per tipe
  sumber. Setiap parameter sumber bisa ditelusuri ke asalnya (halaman buku, berkas
  GEM, atau perhitungan katalog).
