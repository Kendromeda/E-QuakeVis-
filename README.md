# E-QuakeVis

Aplikasi Streamlit untuk menjelajahi katalog gempa Indonesia melalui peta episenter, visualisasi hiposenter 3D dan Slab2, penampang, serta peta publikasi. Filter mencakup waktu, magnitudo, kedalaman, wilayah, dan kotak koordinat yang dapat ditulis atau digambar pada peta.

## Menjalankan secara lokal

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\build_earthquake_store.py
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Perintah pembangun membuat `data/gempa_working.sqlite` dari arsip CSV dan referensi spasial yang disertakan. Berkas SQLite merupakan hasil kerja lokal dan diabaikan oleh Git.

## Instalasi PSHA (opsional)

Tab "Bangun Model PSHA" menjalankan perhitungan dengan OpenQuake 3.26. OpenQuake membutuhkan Python 3.12 dan dipasang di lingkungan terpisah `.venv-oq`. Di Windows, GDAL tidak punya wheel PyPI, jadi gunakan daftar wheel resmi GEM:

```powershell
python -m pip install uv
uv venv --python 3.12 .venv-oq
uv pip install --python .venv-oq\Scripts\python.exe -r https://raw.githubusercontent.com/gem/oq-engine/engine-3.26/requirements-py312-win64.txt
uv pip install --python .venv-oq\Scripts\python.exe --no-deps openquake.engine==3.26.2
.\.venv-oq\Scripts\python.exe scripts\download_gem_idn_model.py
```

Perintah terakhir mengunduh model sumber GEM Indonesia v2024 (lisensi CC BY-NC-SA 4.0, hanya non-komersial) dan memeriksa SHA-256-nya. Datastore OpenQuake bisa berukuran beberapa GB; arahkan ke drive lain dengan `$env:OQ_DATADIR = "D:\oqdata"` sebelum menjalankan perhitungan.

Kondisi situs bawaan adalah batuan Vs30 760 m/s. Untuk memakai Vs30 per situs dari peta global USGS (Heath dkk., 2020), unduh grid-nya sekali (±610 MB) lalu potong ke wilayah Indonesia:

```powershell
curl.exe -L -o data\vs30\download\global_vs30.grd https://apps.usgs.gov/shakemap_geodata/vs30/global_vs30.grd
.\.venv-oq\Scripts\python.exe scripts\prepare_usgs_vs30.py
```

Katalog untuk membangun model sumber sendiri (Tahap B) tidak disimpan di Git dan hanya diperlukan untuk tahap itu:

- Buletin ISC: `.\.venv-oq\Scripts\python.exe scripts\download_isc_bulletin.py` (±5 jam, dapat dilanjutkan bila terputus).
- ISC-GEM v12.1: minta lewat formulir di http://www.isc.ac.uk/iscgem/request_catalogue.php, lalu salin ke `data/psha/iscgem/`.
- Lalu bangun katalog Mw gabungan: `.\.venv-oq\Scripts\python.exe scripts\build_mw_catalog.py`. Model sumber per wilayah dibangun dari langkah 2 wizard (atau `scripts\build_source_model.py`).

Rincian sumber, lisensi, dan langkah perhitungan ada di [data/README.md](data/README.md).

## Data dan lisensi

Asal, metode pemrosesan, dan atribusi setiap katalog serta data spasial dijelaskan di [data/README.md](data/README.md). Kode asli proyek ini tersedia dengan [lisensi MIT](LICENSE). Data dan aset dari pihak lain mengikuti ketentuan sumber masing-masing; berkas [lisensi GeoJSON wilayah](data/LICENSE-GeoJson-Indonesia-38-Provinsi.txt) tetap disertakan.
