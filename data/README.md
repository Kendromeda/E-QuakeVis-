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
