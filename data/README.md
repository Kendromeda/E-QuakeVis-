# Referensi Data Spasial

## Katalog Gempa dan Database Kerja

- Arsip utama: `gempa_master.csv` (format CSV yang dapat diimpor/diekspor)
- Arsip sumber BMKG: `katalog_gempa_bmkg_2008_terbaru.csv`, diunduh dari
  [BMKG FDSN Event Web Service](https://geof.bmkg.go.id/fdsnws/event/1/)
  menggunakan `scripts/download_bmkg_earthquake_catalog.py`. Sumber: BMKG.
- Arsip tervalidasi USGS: `katalog_gempa_usgs_reviewed_m4.csv` (`earthquake`,
  `reviewed`, M>=4; metadata unduhan ada pada berkas `.manifest.json`)
- Database kerja: `gempa_working.sqlite`
- Pembangkit: `scripts/build_earthquake_store.py`

SQLite menjadi sumber query runtime agar aplikasi tidak membaca dan memperkaya
seluruh CSV pada setiap startup. Tabel `earthquakes` menyimpan hasil precompute
`provinsi`, `kabupaten_kota`, `jenis_lokasi`, `jarak_darat_km`, sesar terdekat,
dan `jarak_sesar_km`. Indeks waktu membuat query awal 90 hari hanya memuat data
yang akan ditampilkan. CSV tetap dipertahankan sebagai arsip portabel dan format
ekspor. Jalankan perintah berikut setelah arsip atau referensi spasial berubah:

```powershell
python scripts\build_earthquake_store.py
```

## Sesar PUSGEN 2024

- File: `sesar_pusgen_2024.geojson`
- Layer: `Pusgen_2024_Shallow_Crustal_v6`
- Sumber: BNPB InaRISK ArcGIS REST Service
- URL: https://gis.bnpb.go.id/server/rest/services/inarisk/Faults_new/MapServer/1
- Sistem koordinat: WGS 84 (EPSG:4326)
- Tanggal snapshot: 2026-07-12
- Jumlah fitur: 401
- SHA-256: `D8FEC8D3F795AEA4CC2DD8DE42A0226ED06916B7C10DB28317D70B9CA67E627A`

File disimpan sebagai snapshot lokal agar aplikasi tidak bergantung pada koneksi ke
layanan BNPB saat dijalankan. Atribut `sesar` dan `jarak_sesar_km` pada hasil aplikasi
adalah data turunan berdasarkan garis sesar terdekat. Kedekatan spasial tidak
menyatakan bahwa sesar tersebut menyebabkan suatu kejadian gempa.

## Kawasan Rawan Bencana Gunung Api (BIG)

- File runtime: `krb_gunung_api_indonesia_big.geojson`
- Layer: `Peta Kawasan Rawan Bencana Gunung Api (Area)` (ID 11)
- Sumber: Badan Informasi Geospasial (BIG), Kebijakan Satu Peta
- URL layanan: https://kspservices.big.go.id/satupeta/rest/services/PUBLIK/SUMBER_DAYA_ALAM_DAN_LINGKUNGAN/MapServer/11
- Sistem koordinat: WGS 84 (EPSG:4326)
- Tanggal snapshot: 2026-07-27
- Cakupan: 596 poligon untuk 109 gunung api; kelas KRB I, KRB II, dan KRB III
- Generalisasi: `maxAllowableOffset=0,0005°`, presisi 5 desimal
- SHA-256: `8025B41F6FE3AFF26FC7F27D6263FB50E751E1D462C8951079B99BC69914CD46`
- Pembangkit: `scripts/build_volcano_hazard_areas.py`

Poligon KRB adalah batas resmi kawasan yang berpotensi terdampak bahaya gunung api,
bukan lingkar radius buatan. Aplikasi menampilkan batas tersebut saat layer diaktifkan
dan memusatkan peta ke poligon gunung yang dipilih. Titik pusat untuk perhitungan jarak
diturunkan dari centroid KRB III (atau seluruh KRB bila kelas III tidak tersedia).
Kedekatan spasial tidak menyatakan gunung api sebagai penyebab kejadian gempa.

Snapshot titik lama `gunung_api_indonesia_big.geojson` (127 titik, Atlas Nasional
Indonesia 2021) tetap disimpan sebagai referensi, tetapi tidak digunakan sebagai layer
utama setelah batas KRB tersedia.

## Kontur Kedalaman Slab (Slab2 USGS)

- File: `sum_slab2_dep_contours.in`
- Model: Slab2 — region Sumatra-Java (Sunda), berkas kedalaman `sum_slab2_dep_02.23.18`
- Sumber: U.S. Geological Survey, ScienceBase (Hayes dkk., 2018)
- Referensi: Hayes, G.P., dkk. (2018), *Slab2, a comprehensive subduction zone
  geometry model*, Science, 362(6410), 58–61. DOI: 10.1126/science.aat4723
- URL item: https://www.sciencebase.gov/catalog/item/5aa41834e4b0b1c392eaaf3b
- DOI dataset: https://doi.org/10.5066/F7PV6JNV
- Sistem koordinat: WGS 84 (EPSG:4326)
- Format: kontur GMT multisegmen; kedalaman −20 s/d −660 km (interval 20 km)
- Tanggal snapshot: 2026-07-14
- SHA-256: `FC9D82E7E68F8211AF4B348F1FF40A189BDFB6CDB59099D98747F6C00345C11C`

Kontur menunjukkan kedalaman bidang subduksi (slab) di bawah permukaan. Nilai
kedalaman disimpan sebagai kilometer positif pada tiap garis. Region Sunda
mencakup zona tunjaman Sumatra-Jawa-Bali-Nusa Tenggara hingga lengkung Banda.

### Kontur region Indonesia timur

Berkas kontur resmi Slab2 untuk region lain tidak tersedia secara lokal
(distribusi ScienceBase tidak dapat diunduh otomatis), sehingga kontur ditelusuri
dari grid Slab2 terverifikasi di `data/slab2/<id>.npz` oleh
`scripts/build_slab2_contours.py`, interval 20 km, format GMT yang sama:

| Berkas | Region | Garis | Kedalaman | SHA-256 |
| --- | --- | ---: | --- | --- |
| `slab2/sul_slab2_dep_contours.in` | Sulawesi Utara | 12 | 20–240 km | `F556899EF58066E3F31F6F487D897F46D998090CD01D3C2343BB13FCA8CF7F26` |
| `slab2/hal_slab2_dep_contours.in` | Halmahera / Laut Maluku | 57 | 40–660 km | `B3BE3594FBD103DFA454888B1BA95F261BD3A52E11CF46C8F95F7A52C25A630B` |
| `slab2/png_slab2_dep_contours.in` | Papua (New Guinea) | 13 | 20–160 km | `0BD47C172FD26A1EDD1D7A00AF016131FB76A0FF135AF23FAA238DFE97B04948` |
| `slab2/phi_slab2_dep_contours.in` | Palung Filipina | 26 | 20–220 km | `F4264592D1E21ACE1A612C07F5AC135A90AAF724AA6E659F21973DC82CE77776` |

- Metode: marching squares pada grid asli 0,05°. Garis hanya melewati sel
  dengan node valid (sel tepi dengan tiga node valid ditelusuri sebagai
  segitiga), tanpa smoothing, infill, atau ekstrapolasi ke nodata. Potongan
  ≤ 3 titik yang hanya memotong sudut tepi mask dibuang.
- Validasi: metode yang sama pada `sum.npz` menghasilkan 80 segmen seperti berkas
  resmi Sunda; seluruh 31.785 titik resmi terbentuk ulang (selisih 0,00 km) dan
  hanya 73 titik tambahan (0,2%) di ujung garis tepi mask, maksimum 5,6 km.
- Tanggal pembuatan: 2026-09-19. Reproduksi: `python scripts/build_slab2_contours.py`.

## Grid Permukaan 3D Slab2 USGS

Visualisasi 3D dan penampang A–A' menggunakan grid kedalaman asli Slab2,
bukan permukaan hasil rekonstruksi kontur. Snapshot berada di `data/slab2/`:

| ID | Model | Berkas runtime | Node sumber valid |
| --- | --- | --- | ---: |
| `sum` | Sumatra-Java (Sunda) | `sum.npz` | 115.512 |
| `sul` | Sulawesi | `sul.npz` | 4.539 |
| `hal` | Halmahera | `hal.npz` | 22.485 |
| `png` | New Guinea | `png.npz` | 15.347 |
| `phi` | Philippines | `phi.npz` | 8.425 |

- Sumber resmi: [USGS NEIC catalog separation, direktori Slab2](https://github.com/DOI-USGS/neic-catalog-separation/tree/aae2e073b361b35fdd19c0bd2d155c1443ed2559/neic_catalog_separation/data/Slab2).
  Mirror ini menyertakan grid publikasi Hayes dkk. (2018); unduhan dipatok pada
  commit `aae2e073b361b35fdd19c0bd2d155c1443ed2559` dan diverifikasi SHA-256.
- DOI dataset: [10.5066/F7PV6JNV](https://doi.org/10.5066/F7PV6JNV).
  DOI publikasi: [10.1126/science.aat4723](https://doi.org/10.1126/science.aat4723).
- Tanggal snapshot: 2026-09-14. Versi grid `02.23.18` (sum/sul/hal) dan
  `02.26.18` (png/phi), sesuai nama berkas sumber resmi.
- Resolusi asli: 0,05° longitude × 0,05° latitude; koordinat WGS 84.
- `depth[latitude][longitude]` dalam kilometer positif ke bawah dari permukaan
  laut; nilai z negatif pada sumber hanya dibalik tandanya. Koordinat, presisi
  node, dan mask NaN asli dipertahankan. NaN dikirim ke browser sebagai `null`.
- Transformasi menghapus hanya baris/kolom **paling luar yang seluruhnya NaN**.
  Tidak ada smoothing, infill, interpolasi untuk mengisi mask, atau ekstrapolasi.
  Model Sunda menjadi 764 × 832 node, longitude 91,55–133,10° dan latitude
  −11,45–26,70°; sebagian besar bounding box tetap nodata dan bukan cakupan slab.
- Penampang harus menginterpolasi grid resolusi asli serta memutus garis di
  nodata. Decimation untuk rendering tidak boleh mengganti grid analisis.
- Setiap `.manifest.json` mencatat URL, SHA-256 sumber/keluaran, bentuk grid,
  koordinat, indeks crop, versi, tanggal, dan transformasi. Berkas NetCDF `.grd`
  asli disertakan untuk audit dan reproduksi offline.
- Loader `utils/slab_grid.py` hanya membaca snapshot lokal, memvalidasi grid,
  dan mengembalikan salinan data dari cache immutable. Grid hilang/rusak
  menimbulkan `SlabGridError`; tidak ada fallback geometri sintetis.

Reproduksi (h5py hanya diperlukan untuk konversi, bukan runtime aplikasi):

```powershell
python -m pip install h5py
python scripts/build_slab2_grid.py --source-dir data/slab2
```

Untuk mengunduh ulang dari mirror resmi yang dipatok commit, jalankan tanpa
`--source-dir`. Opsi `--models sum sul` membatasi model yang dikonversi.
SHA-256 sumber harus cocok sebelum konversi dijalankan. Topografi tidak tersedia
dalam grid kedalaman slab ini; bidang 0 km adalah referensi permukaan laut,
bukan DEM atau relief topografi.

## Ruang kerja Hiposenter 3D

Tab **Hiposenter 3D** memakai grid asli di atas untuk surface dan penampang.
Pilihan model **Otomatis** menggunakan grid dengan jumlah sampel profil valid
terbanyak; pemilihan nama model secara manual mempertahankan model tersebut.
Klik **Gambar A–A′** pada peta atau **Gambar A–A′ di 3D**, lalu pilih dua titik.
Pada tampilan 3D, titik yang dipilih diproyeksikan vertikal ke depth 0; bidang
biru transparan saat menggambar hanya alat pemilihan, bukan slab atau topografi.
Koordinat tepat dapat diketik pada kolom A/A′. Corridor adalah setengah lebar
(±km), dengan ujung dibatasi A dan A′. Tombol **Layar penuh** memperbesar model;
**Esc** atau **Keluar layar penuh** mengembalikan layout. Seret untuk rotate,
scroll untuk zoom, Shift+seret atau WASD untuk pan. Gempa yang dipilih di salah
satu grafik ditandai di grafik lainnya. CSV slice memuat jarak sepanjang profil
dan cross-track bertanda positif di sisi kiri A→A′.

Perhitungan jarak menggunakan geodesik bola dengan radius rata-rata bumi
6.371,0088 km (aproksimasi koordinat WGS 84, bukan solusi ellipsoid).
Horizontal 3D menggunakan proyeksi azimutal ekuidistan terpusat pada katalog;
skala vertikal awal 1× dan exaggeration selalu ditampilkan. Distorsi proyeksi
tetap meningkat jauh dari pusat; gunakan filter wilayah untuk analisis lokal.
Depth positif bertambah ke bawah. Profil bilinear disampel maksimum setiap
2 km dari grid resolusi asli; nodata tidak diekstrapolasi atau disambungkan.
Surface 3D ditipiskan dengan mask konservatif agar tidak menutup lubang nodata.
Tidak tersedia DEM numerik lokal, sehingga garis referensi permukaan berada
pada depth 0 km dan tidak menyatakan elevasi topografi atau bathymetri trench.

Modul: `utils/seismic_workspace.py` memproses katalog dan payload;
`utils/slab_grid.py` memuat grid; `utils/seismic_view/geometry.js` menangani
transformasi, corridor, dan interpolasi; `plots.js`, `view3d.js`, `crosssection.js`
merender grafik; `map.js` menangani peta profil; `controller.js` menyimpan state
interaksi. Plotly dibundel dari dependensi Python, tanpa CDN atau unduhan runtime.
Pengaturan profil/layer disimpan pada sessionStorage browser jika diizinkan.

Validasi: `python -m pytest tests -q`; `node --test tests/js/*.test.cjs`.
Uji interaksi pada aplikasi lokal yang berjalan dapat diulang dengan
`playwright-cli run-code --filename tests/e2e/seismic_workspace.js`.

## Palung / Trench Indonesia (Slab2 USGS)

- File: `indonesia_trench_slab2.geojson`
- Pembangkit: `scripts/build_indonesia_trench_geojson.py`
- Sumber: sumbu palung global Slab2 — Hayes dkk. (2018), *Slab2, a comprehensive
  subduction zone geometry model*, USGS data release. Berkas
  `slab2code/library/forplotting/trenches_usgs_2017.csv` dari repositori
  `usgs/slab2`, kolom `lon,lat,az,bound,slab`.
- DOI dataset: https://doi.org/10.5066/F7PV6JNV
- Referensi: Hayes, G.P., dkk. (2018), Science, 362(6410), 58–61.
  DOI: 10.1126/science.aat4723
- Sistem koordinat: WGS 84 (EPSG:4326)
- Cakupan: 4 fitur palung, 708 titik, tiap region Slab2 dipangkas ke wilayah
  Indonesia:
  - `sum` → **Palung Sunda-Banda** (538 titik): Sumatra-Jawa-Nusa Tenggara
    hingga lengkung Banda. Bagian tumbukan kontinen Indo-Burma/Himalaya di utara
    `lat 14°LU` dibuang (bukan palung).
  - `phi` → **Palung Filipina** (61 titik): segmen selatan di lepas Morotai,
    Talaud, dan Sulawesi Utara (dipangkas `lat ≤ 8,5°LU`).
  - `png` → **Palung Papua Utara / New Guinea** (62 titik): di utara Papua,
    dipangkas di batas Indonesia-PNG (`lon ≤ 141,5°BT`).
  - `sul` → **Palung Sulawesi Utara** (47 titik): batas MS/SU di utara lengan
    utara Sulawesi, 119,25–123,43°BT. Region ini tidak ada di
    `trenches_usgs_2017.csv`, sehingga diambil dari berkas pendamping
    `trenches_usgs_2017_depths2.csv` di folder yang sama pada repositori
    `usgs/slab2`.
- Tidak termasuk: Palung Halmahera — Slab2 tidak menerbitkan sumbu palung untuk
  region `hal` di berkas palung mana pun (node grid terdangkal ±33 km, di bawah
  kompleks tumbukan Laut Maluku).
- Tanggal snapshot: 2026-09-19 (tiga palung pertama tidak berubah dari
  snapshot 2026-07-22)
- SHA-256: `C0FACC6BBA77B05945FD8C1E5ADD6A4B109363BBA283CA75DB98F319F4D1D2C1`

Menggantikan berkas `Trench/Sumatra_Java_trench.shp` sebelumnya yang tanpa
atribusi sumber dan hanya mencakup Sumatra-Jawa. Aplikasi memuat GeoJSON ini
(`utils/trench_data.load_trench_geojson`) dan menggambar tiap garis palung dengan
simbol gigi ▲. Kedekatan episentrum ke palung tidak menyatakan hubungan
sebab-akibat. Snapshot ini sekelas sumber dengan kontur kedalaman slab di atas,
sehingga palung dan slab berasal dari model yang sama (Slab2).

## Wilayah Administrasi Indonesia

- File provinsi: `provinsi_indonesia_38.geojson`
- File kabupaten/kota: `kabupaten_kota_indonesia.geojson`
- Sumber repositori: https://github.com/ardian28/GeoJson-Indonesia-38-Provinsi
- Sumber awal yang dicantumkan repositori: Badan Informasi Geospasial (BIG)
- Lisensi repositori: MIT
- Tanggal snapshot: 2026-07-12
- Cakupan: 38 provinsi; 518 fitur kabupaten/kota, 507 memiliki geometri
- SHA-256 provinsi: `1147408DA06361773983128B36E92BB2B942BF53F4FDC97731815F0378797828`
- SHA-256 kabupaten/kota: `EBF19CE23C0B5894E7F29E99BE39F5AAADB50EF6C405098C3CF17887D2B87F9E`

Kolom `provinsi` dan `kabupaten_kota` pada hasil aplikasi ditentukan dari posisi
lintang/bujur terhadap poligon wilayah. Titik di laut atau di luar cakupan poligon
diberi label `Di luar wilayah administrasi`.

Sebelas fitur kabupaten/kota pada sumber memiliki geometri `null`. Titik yang berada
di wilayah tersebut tetap mendapat nama provinsi, tetapi kabupaten/kotanya dapat
bernilai `Tidak teridentifikasi`.

## Kepadatan Penduduk Kecamatan (Jawa & Sumatra bagian selatan)

Cakupan awal enam wilayah Jawa (Jawa Barat, Banten, DKI Jakarta, Jawa Tengah,
Jawa Timur, DI Yogyakarta) diperluas ke lima provinsi Sumatra: Sumatera Barat,
Jambi, Sumatera Selatan, Bengkulu, dan Lampung.

- File gabungan: `kepadatan_penduduk_kecamatan.csv`
- File per kabupaten/kota: `bps_kepadatan_per_kabupaten_kota/*.csv`
- Manifest sumber: `kepadatan_penduduk_sumber_bps.csv`
- Cakupan: 2.985 kecamatan pada 185 kabupaten/kota
  - Jawa Barat: 627 pada 27 (lengkap)
  - Banten: 155 pada 8 (lengkap)
  - Jawa Tengah: 576 pada 35 (lengkap)
  - Daerah Istimewa Yogyakarta: 78 pada 5 (lengkap)
  - Jawa Timur: 636 pada 36 dari 38 — Kabupaten Lamongan dan Kota Mojokerto
    tidak punya tabel BPS yang utuh untuk tahun mana pun
  - DKI Jakarta: 42 pada 5 dari 6 — Kabupaten Administrasi Kepulauan Seribu
    tidak diterbitkan per kecamatan (publikasinya memakai tabel tingkat provinsi)
  - Sumatera Barat: 179 pada 19 dari 19 (lengkap)
  - Jambi: 133 pada 10 dari 11 — Kabupaten Sarolangun tidak punya tabel bersih
  - Sumatera Selatan: 241 pada 17 dari 17 (lengkap)
  - Bengkulu: 117 pada 9 dari 10 — Kabupaten Lebong tidak punya tabel bersih
  - Lampung: 201 pada 14 dari 15 — Kabupaten Lampung Tengah tidak punya tabel
    bersih (satu-satunya tahun berisi nilainya terpotong ribuan atau melonjak rusak)
- Tahun data: 2018–2026, menggunakan tahun terbaru yang lolos audit per
  kabupaten/kota saat pengambilan
- Sumber:
  - Tabel statistik situs BPS kabupaten/kota (mayoritas wilayah, termasuk
    DKI: Kota Jakarta Utara 2024; seluruh 69 wilayah Sumatra dari tabel statistik)
  - Tabel dinamis BPS (DKI: Kota Jakarta Selatan 2021)
  - Publikasi resmi "… Dalam Angka" (Jawa Barat: Sukabumi, Subang, Karawang,
    Kota Cirebon; DKI: Kota Jakarta Timur/Pusat/Barat 2025, Tabel 3.1.1)
- Direktori wilayah→domain BPS: `scripts/bps_region_directory.json`
- Tanggal snapshot: 2026-07-21
- SHA-256 file gabungan: `47631D7A93A49D149AFA76323024A0F0D836544124290281CEBE937950366867`
- SHA-256 manifest: `94C4428B92569123691E622D0EB78C573572C4DC092C766EB48C33B6AE9CA21F`

### Catatan kualitas data Sumatra

Provinsi Sumatra memperlihatkan dua kerusakan sumber BPS yang harus disaring
`scripts/scrape_bps_population_density.py` sebelum sebuah tahun diterima:

- **Terpotong ribuan** — nilai 1.000+ pada payload BPS sesekali dibulatkan ke
  digit ribuannya (`4.219` → `4`), memangkas seluruh distribusi. Karena pemotongan
  hanya mengecilkan nilai, medians tahun terpotong anjlok ≥10×; tahun yang
  mediannya jauh di bawah tahun tersehat (rasio `MEDIAN_DROP_RATIO`) dibuang.
  Kota memakai lantai 50 jiwa/km² (kota tidak pernah sejarang itu), sedangkan
  kabupaten memakai lantai rendah agar kecamatan rawa/transmigrasi yang memang
  jarang (mis. Banyuasin II ~8 jiwa/km²) tetap lolos.
- **Melonjak rusak** — sebagian sel terserialisasi jadi angka mustahil
  (mis. Tanjung Jabung Barat `431.745.205.241.691`). Nilai di atas
  `MAX_PLAUSIBLE_DENSITY` (55.000 jiwa/km²) menandai tahun itu tidak wajar.

Median dipakai (bukan nilai maksimum per sel) agar satu tahun yang melonjak
tidak meracuni pemeriksaan tahun-tahun sehat lainnya.

Setiap baris menyimpan URL dan judul sumber BPS agar nilai dapat diaudit. Daftar
116 sumber, provinsi, tahun data, jumlah kecamatan, dan status fallback tersedia
di manifest. `scripts/scrape_bps_population_density.py` (digerakkan oleh direktori
wilayah) menghasilkan satu file scrape per provinsi di `output/`, lalu
`scripts/build_population_density_dataset.py` menggabungkannya menjadi file
gabungan dan 116 file terpisah.

### Catatan DKI Jakarta

BPS tidak menyediakan satu tabel kepadatan per kecamatan DKI yang lengkap dan
mutakhir, sehingga tiap kota memakai sumber terbaik yang tersedia: Kota Jakarta
Timur, Pusat, dan Barat dari publikasi "… Dalam Angka 2026" (Tabel 3.1.1, data
2025); Kota Jakarta Utara dari tabel statistik (2024); Kota Jakarta Selatan dari
tabel dinamis (2021). Kabupaten Administrasi Kepulauan Seribu belum termasuk
karena kedua kecamatannya tidak diterbitkan secara terpisah.

### Catatan kualitas data BPS

Sebagian tabel BPS merusak nilai kepadatan 1.000 ke atas: `2.875,41` terbaca
sebagai desimal `2,875` lalu dibulatkan menjadi `3`. Kerusakan ada pada data
sumber BPS, bukan pada proses pengambilan, dan berbeda-beda per wilayah *dan*
per tahun — Kabupaten Serang misalnya rusak pada 2024 tetapi utuh pada 2023.

`scripts/scrape_bps_population_density.py` karena itu mengaudit setiap tahun
(jumlah kecamatan, nilai kosong, nilai di bawah 50 jiwa/km², dan nilai identik)
lalu memilih tahun terbaru yang lolos. Tahun yang ditolak beserta alasannya
dicatat pada kolom `catatan_sumber` agar kerusakan tetap terlihat. Skrip membaca
payload data milik BPS sendiri, sehingga tiap kecamatan datang dengan
`kode_wilayah` resminya dan tidak perlu dicocokkan berdasarkan urutan baris.

## Batas Kecamatan (BIG)

- File: `kecamatan_jawa_barat.geojson`, `kecamatan_banten.geojson`,
  `kecamatan_dki_jakarta.geojson`, `kecamatan_jawa_tengah.geojson`,
  `kecamatan_jawa_timur.geojson`, `kecamatan_yogyakarta.geojson`,
  `kecamatan_sumatera_barat.geojson`, `kecamatan_jambi.geojson`,
  `kecamatan_sumatera_selatan.geojson`, `kecamatan_bengkulu.geojson`,
  `kecamatan_lampung.geojson`
- File runtime gabungan: `kecamatan_lightweight.geojson`
- Sumber: Badan Informasi Geospasial (BIG), Batas Administrasi layer kecamatan
- URL: https://geoservices.big.go.id/gis/rest/services/BAPANAS/Batas_Administrasi/MapServer/1
- Edisi sumber: 2022
- Sistem koordinat: WGS 84 (EPSG:4326)
- Cakupan: Jawa Barat 627, Banten 155, DKI Jakarta 44, Jawa Tengah 576,
  Jawa Timur 666, DI Yogyakarta 78, Sumatera Barat 181 (BIG memuat 3 sliver
  tanpa nama), Jambi 144, Sumatera Selatan 241, Bengkulu 129 (BIG memuat 1
  sliver tanpa nama), Lampung 229 fitur kecamatan bernama — sliver tanpa nama
  dibuang saat membangun berkas runtime; total runtime 3.070 fitur
- Generalisasi: dilakukan BIG saat pengambilan (`maxAllowableOffset` 0,001°,
  presisi 6 desimal). Tanpa ini, garis pantai dan pulau-pulau Banten mencapai
  ~52 MB untuk 155 kecamatan — jauh melebihi kebutuhan peta koroplet.
- Tanggal snapshot: 2026-07-20 (Jawa), 2026-07-21 (Sumatra)
- SHA-256 Jawa Barat: `4AD7DD6F1AFEA766082CFC259B2909944F40027941AA98B3ADBB11052990B3D8`
- SHA-256 Banten: `885250C3FD2F9B65A013521D2FD105811CC98BB660CEFC1D3748C5728C2BFF36`
- SHA-256 DKI Jakarta: `AB3CD4AA06716FCBC965EB2C042DC364BD7061411DD5CB2E5AD7B25CE67B259B`
- SHA-256 Jawa Tengah: `F8DD89BA7C16B4B0F0B710053F99AAB11B6AB856CC79F7BE9D80643EE8DC4CA3`
- SHA-256 Jawa Timur: `EED451EAC063C5E69F6CF9288C996B0D28C172516E208B8C08915CE194286483`
- SHA-256 DI Yogyakarta: `F0BBB286D9B09264D2531F049856273999A14C8545A75FFB85B22B7E0D1F49E3`
- SHA-256 Sumatera Barat: `2F1036E1EADA8EAA5D5CA243AF447946A484A2408260BF93F8AF0F0A00A017AA`
- SHA-256 Jambi: `219E63CBB7D692EDCC8633EB8F7377273D2F138D53742184109B33B75A474883`
- SHA-256 Sumatera Selatan: `A7D59AA5DDB73A0D8D91329697CFF01FE20757C26A63F3576DD245571E598901`
- SHA-256 Bengkulu: `C7B1022BE3FA943C146E746FAADD05E13C4626FD8B0E343E178148BA9BF805E9`
- SHA-256 Lampung: `72859E40005DB73869EEFF61C8E5D0E6C469493C5B79BFEFB5D224C002B118DF`
- SHA-256 runtime gabungan: `7DC051C790B39B9A5FE4AEC51AC1C142E50C4A2F1BB5DEAF7E4C35E6E13B8447`

Snapshot diambil ulang dengan `scripts/fetch_kecamatan_boundaries.py`;
`scripts/build_lightweight_subdistrict_geojson.py` menggabungkan snapshot yang
dipakai layer kepadatan menjadi satu berkas runtime dan hanya menyimpan properti
`WADMKK` dan `WADMKC`.

Nama wilayah BPS dan BIG dinormalisasi saat join. Untuk kabupaten/kota, token
`administrasi`/`adm` juga dibuang agar `Kota Adm. Jakarta Barat` (BIG) cocok
dengan `Kota Jakarta Barat` (BPS). Alias kecamatan terdokumentasi di
`utils/population_density.py` menjembatani perbedaan ejaan — dari Jawa
(`Cigemblong`/`Cigemlong` di Lebak, `Ijen`/`Sempol` di Bondowoso) hingga Sumatra
(ejaan Minang/lokal seperti `Lubuak Aluang`/`Lubuk Alung` di Padang Pariaman,
`Koto Tujuh`/`Koto VII` di Sijunjung, `IX Koto`/`Sembilan Koto` di Dharmasraya).
Alias diterapkan simetris ke kedua sisi sehingga hanya mengkanonkan nama;
tabrakan dalam satu kabupaten tertangkap oleh pemeriksaan duplikat saat load.
Seluruh 2.985 baris BPS berhasil dipasangkan dengan batas BIG. Dua poligon BIG
menyandang nama kecamatan yang sama karena terpecah dua (`IV Koto` di Agam,
`Buki Barisan` di Lima Puluh Kota), sehingga join menghasilkan 2.987 fitur —
selisih dua di atas jumlah baris. BIG juga sedikit melebihkan jumlah kecamatan
Agam (17 vs 16 BPS), Lima Puluh Kota (14 vs 13), dan Bengkulu Utara (20 vs 19);
`jumlah_kecamatan` pada direktori wilayah memakai angka resmi BPS.
Seluruh geometri runtime valid dan tidak kosong. File runtime dibangun ulang dengan
`scripts/build_lightweight_subdistrict_geojson.py`. Shapely hanya diperlukan
saat regenerasi aset; aplikasi tidak menambah dependensi runtime baru.

## Kabupaten/Kota Pesisir (garis pantai per wilayah)

- File: `kabupaten_pesisir.json`
- Pembangkit: `scripts/build_coastal_regions.py`
- Turunan dari: `kabupaten_kota_indonesia.geojson` (tidak menambah sumber baru)
- Isi: untuk tiap kabupaten/kota yang berpantai, nama provinsinya dan daftar
  `segmen_pantai` — ruas batas administrasi yang menghadap laut
- Cakupan: 307 kabupaten/kota pesisir, 6.158 segmen pantai
- Tanggal snapshot: 2026-07-21
- SHA-256: `202E85EA734AC5A51199E826F13C5F0895A8F337CC944FAE015FA96B0D71EA23`

Sebuah kabupaten/kota disebut berpantai bila sebagian batasnya menghadap laut.
Tanpa pustaka geometri, tiap ruas batas diprobing ~3 km ke kedua sisi memakai
mesin *point-in-polygon* yang sama dengan penempatan gempa pada wilayah: sebuah
ruas dihitung sebagai pantai bila satu sisinya jatuh di laut (di dalam poligon
kabupaten mana pun tidak) sementara sisi lain di darat. Ruas-ruas itulah garis
pantai wilayah tersebut.

Saat pengguna memfilter kabupaten/kota yang berpantai, aplikasi menampilkan
slider "Jarak maksimum dari pantai (km)" (maksimum 50 km). Filter ini
menambahkan **hanya gempa laut (lepas pantai)** dalam jarak tersebut dari garis
pantai wilayah terpilih ke gempa di daratan wilayah itu sendiri. Gempa darat yang
berada di kabupaten/kota **lain** tidak ikut tampil walau kebetulan dekat pantai
wilayah terpilih — sehingga hasil tetap terbatas pada wilayah yang dipilih plus
seismisitas di lepas pantainya. Batasan ini juga berlaku untuk unduhan/tabel,
bukan hanya peta. Jarak dihitung sebagai jarak permukaan 2D ke ruas pantai
terdekat (mesin yang sama dengan filter jarak ke sesar) dan **bukan** penentu
penyebab gempa. Wilayah tanpa pantai laut (mis. Kota Palembang di tepi Sungai
Musi) tidak memunculkan slider.

## Bahaya Gempa Probabilistik (PSHA)

Tab **Bahaya Gempa (PSHA)** menampilkan peta bahaya yang dihitung dengan
OpenQuake engine. Aplikasi membaca hasil di `data/psha/results/<run_id>/`
(`utils/psha_results.py`). Bila lingkungan `.venv-oq` tersedia, bagian
**Hitung PSHA wilayah baru** di tab tersebut meluncurkan `scripts/run_psha.py`
sebagai proses latar (`utils/psha_jobs.py`): pilih provinsi (atau seluruh
Indonesia) dan jarak grid 0,1/0,2/0,25/0,5°. Folder run berisi tambahan
`app_job.json` (pid, wilayah, perkiraan waktu) dan `run.log`; run baru muncul di
daftar hasil setelah `manifest.json` ditulis. Hasil yang hilang atau tidak konsisten menimbulkan `PSHAResultError`;
tidak ada nilai hazard yang diinterpolasi atau dibuat sintetis.

### Model sumber: GEM IDN v2024.0.0 (default)

- Folder: `psha/gem_idn_v2024.0.0/` (`manifest.json` mencatat URL dan SHA-256;
  isi `job.zip` diekstrak ke `in/`, yang tidak disimpan di Git)
- Model: Peta Sumber dan Bahaya Gempa Indonesia 2024 (PuSGeN, Kementerian PU)
  sebagaimana diimplementasikan GEM untuk mosaik 2026. Sumber: 6 lapisan grid
  kegempaan, megathrust (dua skenario segmentasi S1/S2, bobot 0,9/0,1, masing-
  masing MFD Gutenberg-Richter + karakteristik), sesar dangkal (GR + karakteristik).
  GMPE: BSSA14/CB14/CY14 (kerak), Abrahamson-Gulerce 2020, Zhao 2006, Kuehn 2020
  (interface dan intraslab), dan cabang kerak samudra.
- Sumber: https://cloud.openquake.org/s/wCBESckQYgWgLqx
- Lisensi: **CC BY-NC-SA 4.0**. Hanya untuk penggunaan **non-komersial**, atribusi
  wajib, turunan memakai lisensi yang sama.
- Pengaturan job GEM yang diikuti `scripts/run_psha.py --model v2024.0.0`: jarak
  maksimum 1000 km untuk interface dan 300 km lainnya, `complex_fault_mesh_spacing`
  20 km, `use_rates`, z1.0/z2.5 diturunkan GMPE dari Vs30. Yang tidak diikuti:
  `ps_grid_spacing = 50` (dimatikan karena bias −4 sampai −7%) dan Vs30 per situs
  (`iid-10km.csv`); aplikasi memakai batuan referensi Vs30 760 m/s.
- Pengunduh: `python scripts/download_gem_idn_model.py` (`--verify` untuk
  memeriksa SHA-256)

### Model sumber: GEM IDN v2017.1.0

- Folder: `psha/gem_idn_v2017.1.0/` (`manifest.json` mencatat URL dan SHA-256
  ke-15 berkas; folder `in/` tidak disimpan di Git dan diunduh ulang)
- Model: Irsyam dkk. (2017), *Peta Sumber dan Bahaya Gempa Indonesia 2017*
  (PuSGeN), diimplementasikan penuh di OpenQuake oleh GEM Foundation
- Sumber: https://cloud.openquake.org/s/zsoZY55oe2gSwFZ (halaman produk
  https://www.globalquakemodel.org/product/indonesia-hazard)
- Dokumentasi: https://hazard.openquake.org/gem/pdf/idn-report.pdf
- Lisensi: **CC BY-SA 4.0** — atribusi wajib; turunan memakai lisensi yang sama
- Tanggal snapshot: 2026-10-05
- Pengunduh: `python scripts/download_gem_idn_model.py --version v2017.1.0`
  (`--verify` untuk memeriksa SHA-256); run memakai `--model v2017.1.0`

Inventaris sumber v2017 (`psha/gem_idn_inventory.csv`, ringkasan
`psha/gem_idn_inventory_summary.json`, dibuat oleh
`scripts/inventory_gem_idn_model.py` dengan parser NRML OpenQuake):

| Berkas | Tatanan tektonik | Tipe | Isi |
| --- | --- | --- | --- |
| `ssm/sub/Indonesia_Sub.xml` | Subduction Interface | Complex fault | 13 segmen megathrust, Mmax 7,8–9,2, kedalaman 5–50 km |
| `ssm/crust/Indonesia_Crustal_faults.xml` | Active Shallow Crust | Simple fault | 251 sesar; kedalaman seismogenik umumnya 0–15/17/20 km |
| `ssm/shallow_grid/layer1–2.xml` | Active Shallow Crust | Multi-point (0,1°) | Kegempaan latar, hiposenter 12,6 dan 37,6 km, M 4,0–6,45 |
| `ssm/deep_grid/layer1–4.xml` | Subduction Inslab | Multi-point (0,1°) | Intraslab, hiposenter 60, 125, 175, 200 km, M ≤ 7,75 |

Logic tree GMPE (rerata tertimbang): Active Shallow Crust — BSSA14, CB14,
CY14, Cauzzi 2014, Zhao 2006 (masing-masing 0,2); Interface dan Inslab —
Abrahamson 2015, Parker 2020, Zhao 2006, Kuehn 2020 (masing-masing 0,25).

### Menjalankan perhitungan

OpenQuake 3.26 membutuhkan Python ≥ 3.11 dan berlisensi AGPL-3.0, sehingga
dipasang di lingkungan terpisah `.venv-oq` (tidak ikut `requirements.txt` dan
tidak dibundel ke build desktop). Di Windows, GDAL tidak punya wheel PyPI;
gunakan daftar wheel resmi GEM:

```powershell
uv venv --python 3.12 .venv-oq
uv pip install --python .venv-oq\Scripts\python.exe -r https://raw.githubusercontent.com/gem/oq-engine/engine-3.26/requirements-py312-win64.txt
uv pip install --python .venv-oq\Scripts\python.exe --no-deps openquake.engine==3.26.2
$env:OQ_DATADIR = "D:\oqdata"   # datastore OpenQuake bisa berukuran GB
.venv-oq\Scripts\python.exe scripts\run_psha.py --run-id jabar_0p1 --provinces "Jawa Barat" "Banten" "DKI Jakarta"
```

Run dibuat per wilayah dengan `--provinces`. Tanpa opsi itu seluruh Indonesia
dihitung: grid 0,2° (±3.900 situs) diperkirakan 3–4 jam dan grid 0,1°
(±15.400 situs) ±14 jam pada laptop di bawah.

`scripts/run_psha.py` membuat grid situs daratan (kelipatan spasi, di dalam
poligon `provinsi_indonesia_38.geojson`), menulis `job.ini` yang memakai
berkas model GEM apa adanya, menjalankan OpenQuake, lalu menyimpan:

- `hazard_map.csv` — `lon`, `lat`, satu kolom per `<IMT>-<poe>` (g)
- `hazard_curves-<IMT>.csv` — `lon`, `lat`, `poe-<level>` (peluang terlampaui dalam 50 tahun)
- `sites.csv`, `job.ini`, `manifest.json` (versi OpenQuake, model, wilayah,
  Vs30, waktu hitung, SHA-256 keluaran)

Parameter: PGA, SA(0,2) dan SA(1,0); 10% dan 2% dalam 50 tahun (475 dan
2475 tahun); Vs30 referensi 760 m/s (`--vs30` untuk mengubah; GEM memakai
800 m/s); z1.0 = 30 m, z2.5 = 0,57 km; truncation level 5; jarak maksimum
300 km; `pointsource_distance` 200 km, sama dengan job GEM. Berkas situs GEM
(`sites_idn_res6.csv`) tidak termasuk dalam distribusi, sehingga situs dibuat
sendiri.

Uji percepatan `--ps-grid-spacing 50` pada area Jawa Barat menurunkan nilai
hazard secara sistematis 4–7% (maksimum 13%) dengan percepatan hanya ~1,7×,
sehingga run yang disimpan memakai perhitungan penuh tanpa opsi ini.

| Run | Wilayah | Spasi | Situs | Waktu hitung* |
| --- | --- | --- | ---: | ---: |
| `jabar_0p1` | Jawa Barat, Banten, DKI Jakarta | 0,1° | 382 | 20,6 menit |

\*Laptop 12 core, RAM 16 GB.

### Validasi terhadap SNI 1726:2019 (RSA Cipta Karya)

`scripts/compare_psha_rsa.py --run-id <run>` mengambil nilai SNI 1726:2019
(model PuSGeN 2017) per koordinat dari aplikasi RSA Cipta Karya
(https://rsa.ciptakarya.pu.go.id/2021/, formulir `process.php`, satu permintaan
per 1,5 detik) untuk situs pada kisi `--step` derajat ditambah kota kontrol, lalu
menulis `validation_rsa.csv` dan `validation_rsa.json` ke folder run. Yang
dibandingkan hanya PGA: PGA SNI adalah MCE_G (rerata geometrik, 2% dalam
50 tahun, kelas situs SB), sedangkan Ss/S1 adalah MCE_R *risk-targeted* arah
maksimum sehingga hanya disimpan sebagai referensi.

Hasil `jabar_0p1` (48 situs, diambil 2026-10-06): rasio PGA 2475 tahun run ini
terhadap PGA SNI bermedian **1,17** (P10–P90 1,06–1,22; min 0,90, maks 1,40;
38 situs dalam ±20%). Run ini sistematis lebih tinggi karena:

- GEM v2017.1.0 memperbarui logic tree GMPE (changelog dokumentasi GEM),
  antara lain dengan Parker 2020 dan Kuehn 2020, sehingga tidak identik dengan
  GMPE peta PuSGeN 2017 asli;
- RSA memberi nilai yang sama persis 0,600 g di beberapa situs pantai selatan
  (lokasi rasio terbesar), yang menandakan batas/plafon pada peta SNI;
- Vs30 760 m/s dipakai sebagai wakil kelas SB.

Panel **Validasi terhadap SNI 1726:2019** di tab PSHA menampilkan ringkasan dan
tabel ini bila berkasnya ada.

### Peta Publikasi

Tab **Peta Publikasi** memiliki toggle **Layer bahaya gempa (PSHA)** (pilih run,
parameter, dan periode ulang). Raster disampel per piksel peta pada proyeksi
Web Mercator (`utils/publication_map._render_hazard_overlay`), digambar di bawah
garis sesar/trench, dengan legenda kelas di sidebar dan baris sumber model
(PuSGeN 2017 via GEM IDN, CC BY-SA 4.0) pada daftar sumber data. Bila titik gempa
dimatikan dan tidak ada wilayah/kotak terpilih, cakupan peta mengikuti batas run
PSHA.

Nilai yang ditampilkan adalah peta bahaya *uniform hazard* (rerata logic tree)
pada batuan referensi. Nilai ini **bukan** pengganti parameter desain SNI
1726:2019 (Ss/S1 adalah MCE_R *risk-targeted*); PGA 2475 tahun dapat
dibandingkan dengan PGA (MCE_G) SNI pada kelas situs SB sebagai pemeriksaan.

### Buletin ISC dan homogenisasi magnitudo (Fase 2, Step 5)

- Folder: `psha/isc/`. `manifest.json`, `magnitude_conversion_summary.json`, dan
  kedua PNG disimpan di Git; CSV besar (`isc_events.csv`, `isc_magnitudes.csv`,
  `bmkg_isc_match.csv`, `parts/`) tidak, dan dibuat ulang oleh skrip.
- Sumber: ISC FDSN event service, `includeallmagnitudes=true`, wilayah lat
  -11,5..6,5 dan lon 94,5..141,5. Tiga tahap: 1904–2008-10 semua magnitudo,
  2008-11–sekarang M ≥ 4, dan 2008-11–sekarang M < 4. Total 280.614 event dan
  1.206.980 magnitudo (snapshot 2026-10-10).
- Sitasi: International Seismological Centre (2026), On-line Bulletin,
  https://doi.org/10.31905/D808B830. Bebas dipakai dengan sitasi.
- Pengunduh: `python scripts/download_isc_bulletin.py`. Unduhannya bisa
  dilanjutkan, memakai jeda 15 detik, dan menunggu bila ISC membalas HTTP 429.
- Pita tepi (`--stage margins`): M ≥ 4 di sekeliling kotak di atas sejauh 3,5°
  (lat -15..10, lon 91..145), jendela tahunan. Pita ini dibutuhkan model sumber
  Tahap B: buffer 300 km di sekitar provinsi tepi melewati kotak utama.
- Pencocokan dan regresi: `.venv-oq\Scripts\python.exe scripts/match_bmkg_isc.py`
  (dijalankan di `.venv-oq` agar grafik matplotlib ikut dibuat).
  - Pencocokan memakai origin BMKG (DJA) di buletin ISC (≤5 detik, ≤50 km); bila
    tidak ada, origin utama ISC (≤16 detik, ≤100 km).
  - Mw acuan: GCMT > NEIC Mww > NEIC Mw lain > GFZ > lembaga lain. Mwp, Mw(mB),
    dan Mw BMKG sendiri tidak dipakai sebagai acuan.
  - Regresi General Orthogonal Regression (η = 1), per tipe magnitudo katalog
    BMKG dan per tipe di dalam buletin ISC (DJA, ISC mb, ISC MS, NEIC mb).
- **Batasan:** Mw acuan praktis hanya ada untuk M ≳ 4,8 (persentil 5% = 4,79),
  sehingga regresi terpotong di bagian bawah dan konversi di bawah ±M 4,8
  adalah ekstrapolasi. Gunakan persamaan hanya di dalam `valid_range` dan
  perlakukan konversi magnitudo kecil dengan hati-hati.

### Katalog Mw gabungan (Tahap B)

- Folder: `psha/catalog/`, dibuat oleh `scripts/build_mw_catalog.py` (sekali,
  setelah unduhan ISC dan `match_bmkg_isc.py`). Hanya katalog ringkas ini yang
  dibaca model sumber; buletin ISC mentah tidak dimuat ulang per wilayah.
- Satu Mw per gempa (`utils/mw_catalog.py`): Mw langsung dari momen seismik
  (GCMT > ISC-GEM > NEIC Mww > NEIC Mw > GFZ > lainnya, bukan Mw BMKG) > proxy
  Mw ISC-GEM > konversi. Konversi memakai regresi GOR di dalam buletin ISC
  (DJA, ISC mb, ISC MS, NEIC mb termasuk kode lama USCGS/NEIS), hanya di dalam
  rentang data regresinya; MS dibatasi ≤ 6,1 karena hubungan MS–Mw bilinear
  (Scordilis, 2006). Bila beberapa konversi berlaku, Mw adalah rerata berbobot
  1/σ². Rumus MLv katalog BMKG (kemiringan 0,42, terpotong) tidak dipakai.
- Pembersihan: episenter utama di luar kotak unduhan dibuang, laporan ganda satu
  gempa (≤ 16 detik, ≤ 100 km) dibuang, gempa yang hanya ada di ISC-GEM
  ditambahkan, kedalaman tetap (0/10/33 km) ditandai, dan gempa 24 bulan
  terakhir ditandai "kemungkinan belum direview ISC".
- Keluaran: `mw_catalog.csv` dan CSV pemrosesan (`*_processing`, `*_sources`,
  `*_conversions`, `*_by_decade`, `*_excluded_review`, `*_duplicates`), serta
  semuanya dalam satu workbook `analisis_katalog_mw.xlsx`.

### Model sumber Tahap B (per kotak wilayah)

- Pembangun: `scripts/build_source_model.py --name <nama> --bounds W S E N`
  (di `.venv-oq`, memakai HMTK), atau tombol di langkah 2 wizard. Keluaran di
  `psha/source_models/<nama>/`, dijelaskan di `00_files.csv`, dan semua tabel
  dalam satu workbook `analisis_model_sumber.xlsx` (satu sheet per langkah).
- Langkah: wilayah sumber = kotak situs + buffer 300 km → seleksi katalog
  (Mw ≥ 4) → deklasterisasi Gardner–Knopoff (1974) → kelengkapan Stepp (1972)
  → a-b Weichert (1980) per enam lapisan kedalaman GEM/PuSGeN (0–25, 25–50,
  50–100, 100–150, 150–200, 200–300 km; Mw ≥ 4,5) → penghalusan Frankel (1995)
  kernel Gaussian 50 km, grid 0,1° → sesar kerak dan megathrust PuSGeN 2024 dari
  GEM IDN v2024 dalam 300/1000 km dari kotak → `nrml/*.xml` dan `ssmLT.xml`.
- Gempa latar memakai pengaturan rupture lapisan GEM (bidang nodal, WC1994,
  kedalaman hiposenter) dan Mmax 6,5 (dangkal, gempa lebih besar di sesar) /
  7,8 (intraslab). Lapisan dengan < 50 gempa di periode lengkap memakai b
  regional. Gempa di bawah 300 km dihitung a-b-nya tetapi tidak dimodelkan.
- Hazard: `scripts/run_psha.py --source-model psha/source_models/<nama>`; GMPE,
  tabel reqv, dan jarak maksimum tetap mengikuti GEM IDN v2024.

### Vs30 USGS per situs (opsional)

- Sumber: USGS Global Hybrid Vs30 (Heath dkk., 2020, *Earthquake Spectra* 36(3),
  1570–1584), grid 30 detik busur, https://apps.usgs.gov/shakemap_geodata/vs30/
  `global_vs30.grd` (netCDF4, ±610 MB; disimpan di `vs30/download/`, tidak di Git).
- `scripts/prepare_usgs_vs30.py` memotongnya ke lat -15..10, lon 91..145 menjadi
  `vs30/usgs_vs30_indonesia.npz` (int16, 5 MB, tidak di Git); `vs30/manifest.json`
  mencatat URL, SHA-256, dan sitasi. Di Indonesia nilainya 180–900 m/s.
- Dipakai bila kondisi situs "Vs30 USGS per situs" dipilih di wizard
  (`run_psha.py --vs30-source usgs`): `sites.csv` run berisi kolom `vs30`
  (seperti `iid-10km.csv` GEM), z1.0/z2.5 diturunkan GMPE dari Vs30. Hasilnya
  bahaya di **permukaan**, tidak setara peta SNI di batuan. Nilainya proxy
  kemiringan topografi, bukan pengukuran.
- Grid situs 0,01° (±1 km) tersedia di wizard untuk wilayah kecil (batas 10.000
  situs); grid dibangun hanya di dalam kotak wilayah.

### Katalog ISC-GEM v12.1

- Folder: `psha/iscgem/` (`isc-gem-cat.csv`, `isc-gem-suppl.csv`,
  `isc-gem-cat.kmz`). File-file ini tidak disimpan di Git. Mintalah lewat
  formulir (dengan CAPTCHA) di http://www.isc.ac.uk/iscgem/request_catalogue.php,
  lalu salin ke folder ini.
- Isi: katalog instrumental global 1904–2021 dengan Mw homogen. Kolom `s`
  (`mw_src`) bernilai `d` bila Mw dihitung dari momen seismik (GCMT atau
  literatur), atau `p` bila Mw adalah proxy hasil konversi MS/mb oleh ISC-GEM.
  Suplemen berisi event yang lokasi atau magnitudonya kurang andal (kualitas D).
- Wilayah katalog BMKG: 8.124 event di katalog utama (1905–2021; 6.111 `d`,
  2.013 `p`) dan 723 di suplemen. 99,6% event memiliki `eventid` yang sama
  dengan event ISC di `psha/isc/`.
- Pembaca: `utils/iscgem.py`. Di `scripts/match_bmkg_isc.py`, Mw `d` dari
  katalog utama menjadi Mw acuan prioritas kedua setelah GCMT. Mw proxy tidak
  dipakai sebagai acuan regresi agar tidak melingkar, tetapi tetap tersedia
  untuk katalog Mw gabungan.
- Lisensi: CC BY-SA 3.0. Sitasi: International Seismological Centre (2025),
  ISC-GEM Earthquake Catalogue, https://doi.org/10.31905/D808B825, ditambah
  Storchak dkk. (2013, 2015) dan Di Giacomo dkk. (2018). Untuk parameter
  magnitudo, sitasi juga Di Giacomo dkk. (2015a).

### Wizard "Bangun Model PSHA" (GMPE dan logic tree pilihan pengguna)

Tab **Bangun Model PSHA** (`psha_wizard.py`) memandu pembuatan PSHA langkah
demi langkah. Setiap langkah menjelaskan apa yang diputuskan dan harus
dikonfirmasi sebelum langkah berikutnya terbuka. Mengubah langkah yang sudah
dikonfirmasi membatalkan konfirmasi langkah-langkah sesudahnya.

1. **Wilayah dan kondisi situs**: provinsi, jarak grid, dan Vs30 760 m/s.
   Ditampilkan jumlah situs, peta titik, dan perkiraan waktu hitung.
2. **Model sumber gempa**: Tahap A memakai model sumber GEM IDN v2024 atau
   v2017 (isi berkas dan tatanan tektoniknya ditampilkan). Tahap B (model
   sumber sendiri dari katalog) akan ditambahkan.
3. **GMPE dan logic tree**: kandidat per tatanan tektonik beserta data asal,
   rentang M/jarak, dan siapa yang memakainya (`utils/gmpe_catalog.py`).
   Tersedia preset GEM v2024/v2017, bobot sama rata, validasi (jumlah bobot,
   GMPE tunggal, varian ganda), dan grafik **trellis** (median vs jarak) yang
   dihitung oleh `scripts/gmpe_trellis.py` di `.venv-oq`.
4. **Tinjau dan hitung**: ringkasan semua pilihan, jumlah realisasi, perkiraan
   waktu, peringatan RAM (OpenQuake perlu < 80%), kotak centang "sudah meninjau",
   lalu peluncuran `scripts/run_psha.py --gmm-lt-json ...`. Logic tree ditulis
   ke `gmmLT.xml` di folder run dan dicatat di `manifest.json`
   (`gmpe_logic_tree`).
5. **Hasil**: ringkasan nilai per parameter dan perbandingan rasio dengan run
   lain di wilayah dan grid yang sama.

Proyek disimpan di `psha/projects/<id>.json` (`utils/psha_project.py`): pilihan
per langkah, catatan alasan pengguna, waktu konfirmasi, dan `run_id`.
