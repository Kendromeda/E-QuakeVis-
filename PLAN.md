# Plan: Web App Interaktif Data Gempa Indonesia

## Goal
Membangun web app berbasis Python (Streamlit) yang memungkinkan user meng-upload data gempa milik sendiri, memvisualisasikannya sebagai titik merah di peta Indonesia, memfilter berdasarkan kedalaman/waktu/magnitudo, dan mendownload hasil filter sebagai CSV — mirip alur USGS tapi dijalankan lokal (localhost) dengan data milik sendiri.

## Context
Untuk penggunaan pribadi/riset, dijalankan lokal via `streamlit run app.py`, tidak perlu hosting/auth. Data sumber sudah dimiliki user dalam bentuk CSV/Excel (statis, <100rb baris) dengan kolom baku: tanggal, jam, lintang, bujur, kedalaman, magnitudo — tidak ada kolom nama wilayah. File di-upload manual oleh user lewat UI setiap sesi (bukan file tetap di project), karena user ingin fleksibel ganti-ganti file.

## Process Overview
1. Setup project (struktur folder, dependencies, virtual environment).
2. Bangun modul load & validasi file upload (cek kolom baku, tolak jika tidak sesuai).
3. Bangun modul cleaning data (buang baris invalid, gabungkan tanggal+jam jadi datetime).
4. Bangun sidebar filter (slider kedalaman, slider magnitudo, date range waktu) + tombol "Terapkan".
5. Bangun peta Indonesia dengan titik merah (ukuran proporsional magnitudo) + popup info per titik.
6. Bangun tabel data & ringkasan statistik di bawah peta, sinkron dengan hasil filter.
7. Bangun tombol download CSV dari hasil filter.
8. Implementasi edge case: upload gagal, data invalid, hasil filter kosong.
9. Uji end-to-end dengan sample data buatan sendiri.
10. Polish (styling, pesan error, instruksi pemakaian di UI).

## Detailed Steps

### Step 1: Setup Project
**What happens:** Membuat struktur folder project dan environment Python, install dependencies.
**Input:** Python 3.9+ terinstall.
**Output:** Struktur folder siap, `requirements.txt` terisi, virtual environment aktif.
**Decisions:** Struktur folder yang direkomendasikan:
```
Database_Interaktif/
├── app.py                  # entry point Streamlit
├── requirements.txt
├── utils/
│   ├── data_loader.py      # load, validasi, cleaning data
│   ├── map_builder.py      # bangun peta folium + titik gempa
│   └── stats.py            # hitung ringkasan statistik
└── PLAN.md
```
**Owner:** User (dengan bantuan Claude Code untuk implementasi).
**Notes:** Dependencies: `streamlit`, `pandas`, `folium`, `streamlit-folium`, `openpyxl` (untuk baca file .xlsx).

### Step 2: Modul Load & Validasi Upload
**What happens:** User klik tombol upload di UI (`st.file_uploader`), pilih file CSV/Excel. App membaca file, cek apakah 6 kolom baku ada: `tanggal`, `jam`, `lintang`, `bujur`, `kedalaman`, `magnitudo`.
**Input:** File CSV/Excel dari user.
**Output:** DataFrame mentah, atau pesan error jika kolom tidak sesuai.
**Decisions:** Jika ada kolom yang hilang/salah nama → tampilkan `st.error()` yang menyebutkan secara eksplisit kolom mana yang hilang, lalu `st.stop()` (tidak lanjut proses). Nama kolom harus persis sesuai format baku (case-sensitive), tidak ada fitur mapping kolom.
**Notes:** Deteksi format file dari ekstensi (`.csv` → `pd.read_csv`, `.xlsx`/`.xls` → `pd.read_excel`).

### Step 3: Modul Cleaning Data
**What happens:** Setelah kolom tervalidasi, bersihkan data secara otomatis tanpa notifikasi ke user.
**Input:** DataFrame mentah dari Step 2.
**Output:** DataFrame bersih siap difilter, dengan kolom tambahan `waktu` (datetime gabungan tanggal+jam).
**Decisions:**
- Gabungkan kolom `tanggal` + `jam` → kolom `waktu` (datetime).
- Buang baris dengan nilai kosong/tidak valid (non-numeric) di kolom `lintang`, `bujur`, `kedalaman`, `magnitudo`, atau `waktu` yang gagal di-parse.
- Buang baris dengan koordinat di luar bounding box Indonesia (asumsi: lintang -11 s/d 6, bujur 95 s/d 141 — **perlu dikonfirmasi/disesuaikan user**, lihat Open Questions).
- Proses ini silent (tidak ada notifikasi jumlah baris yang dibuang), sesuai kesepakatan interview.
**Notes:** Simpan hasil cleaning di `st.session_state` supaya tidak perlu reload/reclean tiap interaksi filter.

### Step 4: Sidebar Filter
**What happens:** Setelah data berhasil di-upload & dibersihkan, sidebar menampilkan 3 filter dengan rentang default otomatis dari min-max data:
- Slider kedalaman (km): min-max dari data.
- Slider magnitudo (SR): min-max dari data, step 0.1.
- Date range picker waktu: min-max tanggal dari data.
- Tombol "Terapkan".
**Input:** DataFrame bersih dari Step 3.
**Output:** Nilai filter yang dipilih user, tersimpan di `st.session_state` saat tombol "Terapkan" diklik.
**Decisions:** Peta & tabel TIDAK update otomatis saat slider digeser — hanya update setelah klik "Terapkan" (sesuai kesepakatan, supaya tidak berat re-render tiap geser slider). Sebelum "Terapkan" pertama kali diklik, area utama menampilkan peta kosong Indonesia + pesan instruksi ("Atur filter lalu klik Terapkan").
**Notes:** Gunakan `st.session_state` dengan flag `has_applied` (boolean) dan `filtered_df` untuk menyimpan state antar rerun Streamlit.

### Step 5: Peta & Titik Gempa
**What happens:** Setelah "Terapkan" diklik dan ada hasil, render peta Indonesia (folium) dengan titik merah untuk tiap gempa hasil filter.
**Input:** `filtered_df` dari Step 4.
**Output:** Peta interaktif ter-render di halaman utama (`st_folium`).
**Decisions:**
- Basemap center default: koordinat tengah Indonesia (`-2.5, 118`), zoom level `5`.
- Tiap titik: `folium.CircleMarker`, warna merah tetap, radius proporsional ke magnitudo (misal `radius = magnitudo * 2`, bisa disesuaikan saat testing visual).
- Klik titik → popup muncul berisi: tanggal, jam, magnitudo, kedalaman, koordinat (lintang, bujur).
**Notes:** Untuk dataset besar (mendekati 100rb baris hasil filter), render ribuan `CircleMarker` sekaligus di folium bisa lambat — jika terjadi, pertimbangkan `folium.plugins.MarkerCluster` atau ganti ke `pydeck` (lihat Open Questions).

### Step 6: Tabel Data & Ringkasan Statistik
**What happens:** Di bawah peta, tampilkan tabel (`st.dataframe`) berisi seluruh baris hasil filter, dan ringkasan statistik di atasnya.
**Input:** `filtered_df` yang sama dengan yang dipakai untuk peta (harus selalu sinkron).
**Output:** Tabel data + 3 metric card: jumlah gempa, magnitudo rata-rata, magnitudo maksimum.
**Decisions:** Gunakan `st.columns(3)` + `st.metric()` untuk ringkasan statistik, diletakkan di atas peta atau di atas tabel (posisi bisa disesuaikan saat implementasi).
**Notes:** Tabel dan peta harus bersumber dari `filtered_df` yang sama persis supaya jumlah titik di peta = jumlah baris di tabel = angka "jumlah gempa" di ringkasan.

### Step 7: Tombol Download
**What happens:** Tombol "Download CSV" muncul jika `filtered_df` tidak kosong, mengunduh persis data yang sedang tampil (hasil filter aktif).
**Input:** `filtered_df`.
**Output:** File CSV terdownload ke komputer user, nama file misal `gempa_filtered_YYYYMMDD.csv`.
**Decisions:** Gunakan `st.download_button` dengan data hasil `filtered_df.to_csv(index=False).encode('utf-8')`.
**Notes:** Tombol ini disembunyikan/dinonaktifkan total jika hasil filter kosong (lihat Step 8).

### Step 8: Edge Case Handling
**What happens:** Menangani 3 skenario gagal/khusus yang sudah disepakati.
**Input:** Berbagai state aplikasi (upload gagal, data invalid, filter kosong).
**Output:** Pesan/UI yang sesuai untuk tiap skenario.
**Decisions:** Lihat detail lengkap di bagian **Edge Cases and Failure Modes** di bawah.
**Notes:** Semua edge case ini harus diuji manual satu per satu sebelum dianggap selesai (Step 9).

### Step 9: Testing End-to-End
**What happens:** Uji seluruh alur pakai data sample buatan sendiri (mencakup data valid, data dengan baris invalid, dan filter yang sengaja dibuat tidak menemukan hasil).
**Input:** File CSV sample yang sengaja dibuat untuk testing (bukan data asli).
**Output:** Konfirmasi seluruh alur (upload → validasi → cleaning → filter → peta/tabel/stats → download) berjalan sesuai rencana.
**Decisions:** Buat minimal 2 file sample: (a) file valid lengkap, (b) file dengan beberapa baris rusak/kolom hilang, untuk menguji validasi & cleaning.
**Notes:** Testing ini dilakukan sebelum user mencoba upload data gempa asli miliknya.

### Step 10: Polish
**What happens:** Perbaikan kecil pada UX: pesan instruksi awal, label filter dalam Bahasa Indonesia, format angka/tanggal yang rapi di tabel dan popup.
**Input:** App yang sudah berfungsi penuh dari Step 1-9.
**Output:** App siap dipakai sehari-hari.
**Decisions:** Opsional, bisa di-skip di iterasi pertama jika waktu terbatas.
**Notes:** Tidak mengubah logika inti, murni kosmetik.

## Edge Cases and Failure Modes
- **Upload file dengan kolom tidak sesuai format baku** → `st.error()` menyebutkan kolom yang hilang, proses dihentikan (`st.stop()`), user diminta upload ulang.
- **Upload file bukan CSV/Excel (format salah)** → tangkap exception saat `pd.read_csv`/`read_excel`, tampilkan pesan error umum "File tidak bisa dibaca, pastikan format CSV atau Excel yang valid".
- **Baris data dengan nilai kosong/non-numeric di kolom penting** → dibuang otomatis secara silent saat cleaning (Step 3), tidak ada notifikasi ke user (sesuai kesepakatan).
- **Koordinat di luar wilayah Indonesia** → dibuang otomatis via bounding box check (asumsi rentang, lihat Open Questions).
- **Hasil filter kosong (0 baris)** → tampilkan pesan "Tidak ada data ditemukan", peta ditampilkan kosong (tetap center Indonesia), tombol download disembunyikan/dinonaktifkan.
- **User klik "Terapkan" sebelum upload file** → tombol filter/Terapkan tidak muncul sama sekali sebelum upload berhasil (area utama hanya menampilkan prompt upload).
- **Dataset hasil filter sangat besar (puluhan ribu titik)** → berpotensi render peta lambat; mitigasi awal: batasi test dengan data real, siapkan fallback ke `MarkerCluster`/`pydeck` jika terbukti lambat.

## Dependencies and Requirements
- Python 3.9 atau lebih baru.
- Package: `streamlit`, `pandas`, `folium`, `streamlit-folium`, `openpyxl`.
- File data gempa sample (untuk testing) dan file data gempa asli (untuk pemakaian nyata) dengan kolom baku: `tanggal`, `jam`, `lintang`, `bujur`, `kedalaman`, `magnitudo`.
- Tidak perlu koneksi internet untuk basemap jika pakai tile OpenStreetMap default folium — **catatan:** folium default pakai tile online (OpenStreetMap), jadi tetap butuh internet untuk load tile peta meski app jalan di localhost.

## Open Questions
- **Bounding box koordinat Indonesia** untuk validasi "di luar wilayah Indonesia" masih asumsi (lintang -11 s/d 6, bujur 95 s/d 141) — perlu dikonfirmasi apakah rentang ini sudah tepat atau perlu disesuaikan (misal untuk memasukkan wilayah perbatasan/ZEE).
- **Nama kolom persis** di file asli belum diverifikasi huruf besar/kecil dan ejaannya (`tanggal` vs `Tanggal`, `magnitudo` vs `magnitude`) — perlu dicek saat user siap upload file asli, supaya validasi kolom di Step 2 tidak salah tolak.
- **Formula ukuran titik** (`radius = magnitudo * 2`) adalah titik awal, kemungkinan perlu di-tuning visual setelah lihat hasil render dengan data asli.
- **Performa render** untuk hasil filter dengan jumlah titik sangat besar (mendekati batas 100rb baris) belum diuji — perlu dicoba dengan data asli untuk memastikan folium masih responsif.

## Success Criteria
- User bisa upload file CSV/Excel miliknya sendiri dan app berhasil membaca serta memvalidasi kolomnya.
- User bisa mengatur 3 filter (kedalaman, magnitudo, rentang waktu) dan melihat titik merah muncul di peta hanya setelah klik "Terapkan".
- Ukuran titik merah terlihat proporsional terhadap magnitudo, dan klik titik menampilkan info detail yang benar.
- Tabel data dan ringkasan statistik selalu sinkron dengan jumlah titik yang tampil di peta.
- Tombol download menghasilkan file CSV yang isinya persis sama dengan hasil filter yang sedang ditampilkan.
- Filter yang tidak menemukan hasil menampilkan pesan yang jelas dan tidak menyediakan tombol download yang membingungkan.
