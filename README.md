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

## Data dan lisensi

Asal, metode pemrosesan, dan atribusi setiap katalog serta data spasial dijelaskan di [data/README.md](data/README.md). Kode asli proyek ini tersedia dengan [lisensi MIT](LICENSE). Data dan aset dari pihak lain mengikuti ketentuan sumber masing-masing; berkas [lisensi GeoJSON wilayah](data/LICENSE-GeoJson-Indonesia-38-Provinsi.txt) tetap disertakan.
