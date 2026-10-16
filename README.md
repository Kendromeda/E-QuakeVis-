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
gh auth login
.\.venv-oq\Scripts\python.exe scripts\setup_psha_data.py
```

`setup_psha_data.py` menyiapkan semua data PSHA dan melewati yang sudah ada:

- model sumber GEM Indonesia v2024 (lisensi CC BY-NC-SA 4.0, hanya non-komersial), dari GEM;
- Vs30 USGS wilayah Indonesia (Heath dkk., 2020) untuk pilihan "Vs30 USGS per situs";
- katalog Mw gabungan ISC/ISC-GEM untuk model sumber sendiri (Tahap B).

Dua yang terakhir diambil siap pakai dari GitHub Release (`data/psha/data_release.json`, diperiksa SHA-256). Repo ini privat, jadi perlu `gh auth login` sekali. Untuk membangunnya ulang dari sumber asli (grid USGS 610 MB; buletin ISC berjam-jam, ditambah ISC-GEM dari formulir http://www.isc.ac.uk/iscgem/request_catalogue.php bila tersedia), tambahkan `--from-source`.

Datastore OpenQuake bisa berukuran beberapa GB; arahkan ke drive lain dengan `$env:OQ_DATADIR = "D:\oqdata"` sebelum menjalankan perhitungan. Model sumber per wilayah (Tahap B) dibangun dari langkah 2 wizard.

Rincian sumber, lisensi, dan langkah perhitungan ada di [data/README.md](data/README.md).

## Data dan lisensi

Asal, metode pemrosesan, dan atribusi setiap katalog serta data spasial dijelaskan di [data/README.md](data/README.md). Kode asli proyek ini tersedia dengan [lisensi MIT](LICENSE). Data dan aset dari pihak lain mengikuti ketentuan sumber masing-masing; berkas [lisensi GeoJSON wilayah](data/LICENSE-GeoJson-Indonesia-38-Provinsi.txt) tetap disertakan.
