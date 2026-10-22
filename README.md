# E-QuakeVis

E-QuakeVis adalah aplikasi web lokal berbasis [Streamlit](https://streamlit.io) untuk menjelajahi katalog gempa Indonesia, menghitung bahaya gempa probabilistik (PSHA) dengan [OpenQuake](https://github.com/gem/oq-engine), dan membuat peta siap cetak.

Katalog BMKG dan USGS, sesar PuSGeN 2024, tunjaman dan kontur Slab2, gunung api, serta batas wilayah sudah disertakan, jadi aplikasi bisa langsung dipakai tanpa koneksi ke layanan eksternal (kecuali basemap).

## Fitur

| Tab | Isi |
| --- | --- |
| **Peta Sebaran** | Episenter pada peta interaktif dengan basemap pilihan, sesar, tunjaman, kontur slab, gunung api, stasiun seismik, dan kepadatan penduduk. Area minat bisa digambar langsung di peta. |
| **Hiposenter 3D** | Hiposenter dalam ruang 3D bersama permukaan Slab2, termasuk penampang tegak lurus palung untuk melihat zona Wadati-Benioff. |
| **Bahaya Gempa (PSHA)** | Peta bahaya PGA dan SA per periode ulang, kurva hazard per situs, dan ekspor grid ke CSV. |
| **Bangun Model PSHA** | Wizard bertahap untuk menjalankan OpenQuake: pilih wilayah dan Vs30, pilih model sumber GEM Indonesia atau bangun model sendiri dari katalog Mw, lalu atur logic tree GMPE. |
| **Statistik** | Ringkasan dan grafik katalog yang sedang difilter. |
| **Data Kejadian** | Tabel kejadian hasil filter, dapat diunduh sebagai CSV. |
| **Peta Publikasi** | Lembar peta A4 sampai A2 berformat PNG dan PDF, lengkap dengan legenda, skala numerik, panah utara, peta lokasi, logo instansi, dan daftar sumber data. Tersedia template standar dan template Tata Ruang (RTRW). |

Filter di sidebar mencakup waktu, magnitudo, kedalaman, provinsi, kabupaten/kota, jarak ke sesar, gunung api, dan garis pantai, serta kotak koordinat. Katalog sendiri dalam format CSV atau Excel dapat diimpor dari sidebar.

## Menjalankan secara lokal

Butuh Python 3.10 atau lebih baru. Contoh di bawah memakai PowerShell di Windows.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\build_earthquake_store.py
.\.venv\Scripts\python.exe -m streamlit run app.py
```

`build_earthquake_store.py` membangun database kerja `data/gempa_working.sqlite` dari arsip CSV dan data spasial yang disertakan. Jalankan ulang setiap kali arsip katalog berubah. Berkas SQLite ini hanya ada di komputer lokal dan tidak disimpan di Git.

## Modul PSHA (opsional)

Tab **Bangun Model PSHA** menjalankan OpenQuake 3.26, yang membutuhkan Python 3.12 di lingkungan terpisah `.venv-oq`. GDAL tidak punya wheel PyPI untuk Windows, jadi pasang dari daftar wheel resmi GEM:

```powershell
python -m pip install uv
uv venv --python 3.12 .venv-oq
uv pip install --python .venv-oq\Scripts\python.exe -r https://raw.githubusercontent.com/gem/oq-engine/engine-3.26/requirements-py312-win64.txt
uv pip install --python .venv-oq\Scripts\python.exe --no-deps openquake.engine==3.26.2
.\.venv-oq\Scripts\python.exe scripts\setup_psha_data.py
```

`setup_psha_data.py` menyiapkan data berikut dan melewati yang sudah ada:

- model sumber GEM Indonesia v2024, diunduh dari GEM;
- Vs30 USGS untuk wilayah Indonesia (Heath dkk., 2020), untuk pilihan "Vs30 USGS per situs";
- katalog Mw gabungan ISC dan ISC-GEM, untuk membangun model sumber sendiri.

Vs30 dan katalog Mw diunduh siap pakai dari GitHub Release proyek ini dan diperiksa dengan SHA-256. Tambahkan `--from-source` untuk membangunnya ulang dari sumber aslinya. Cara ini jauh lebih lama: grid USGS berukuran 610 MB dan unduhan buletin ISC memakan waktu berjam-jam.

Datastore OpenQuake bisa mencapai beberapa GB. Untuk menyimpannya di drive lain, atur `$env:OQ_DATADIR = "D:\oqdata"` sebelum menjalankan perhitungan.

## Aplikasi desktop Windows

Folder [`desktop_nuitka/`](desktop_nuitka/README.md) berisi skrip untuk mengemas aplikasi menjadi EXE Windows dengan Nuitka. EXE tersebut menjalankan server lokal di `127.0.0.1` dan membuka aplikasi di browser.

## Struktur proyek

```text
app.py        Aplikasi Streamlit (seluruh tab dan sidebar)
utils/        Pemuatan data, peta, peta publikasi, dan hasil PSHA
scripts/      Pengunduh data, pembangun database, dan alat PSHA
data/         Katalog, data spasial, model, dan hasil PSHA
tests/        Pengujian unit (pytest)
```

## Pengujian

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest
```

## Data dan lisensi

Kode proyek ini dirilis dengan [lisensi MIT](LICENSE).

Data dari pihak lain tetap mengikuti ketentuan sumber masing-masing. Asal, metode pemrosesan, lisensi, dan atribusi setiap dataset dijelaskan di [data/README.md](data/README.md). Ringkasnya:

- Katalog gempa: BMKG dan USGS.
- Sesar: PuSGeN 2024, melalui layanan GIS BNPB InaRISK.
- Tunjaman dan kontur slab: Slab2 USGS (Hayes dkk., 2018).
- Batas wilayah: GeoJSON 38 Provinsi, berlisensi MIT ([berkas lisensi](data/LICENSE-GeoJson-Indonesia-38-Provinsi.txt)).
- Kepadatan penduduk: BPS.
- Model sumber GEM Indonesia v2024 berlisensi **CC BY-NC-SA 4.0** dan hanya boleh dipakai untuk keperluan non-komersial. Hasil PSHA di `data/psha/results/` yang dihitung dengan model ini mengikuti lisensi yang sama.
- OpenQuake berlisensi AGPL-3.0. Karena itu OpenQuake dijalankan di lingkungan terpisah dan tidak ikut dibundel ke dalam aplikasi.
