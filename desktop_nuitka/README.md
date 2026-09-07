# E-QuakeVis - Desktop Windows

Folder ini membungkus aplikasi Streamlit sebagai aplikasi Windows menggunakan
Nuitka. EXE menjalankan server lokal hanya pada `127.0.0.1`, memilih port kosong,
kemudian membuka aplikasi di browser default.

## Menjalankan tanpa build

```powershell
.\.venv\Scripts\python.exe .\desktop_nuitka\launcher.py
```

Untuk pengujian otomatis, browser dapat dinonaktifkan dan port dapat ditetapkan:

```powershell
$env:E_QUAKEVIS_NO_BROWSER = "1"
$env:E_QUAKEVIS_PORT = "8501"
.\.venv\Scripts\python.exe .\desktop_nuitka\launcher.py
```

## Build yang direkomendasikan

Mulai dengan mode standalone agar kesalahan dependensi mudah diperiksa:

```powershell
.\desktop_nuitka\build.ps1
```

Skrip memakai `uv` dengan certificate store Windows apabila tersedia, kemudian
jatuh kembali ke `pip` bila `uv` tidak terpasang.

Jika build sebelumnya terputus setelah backend C mulai, cache objek dapat dipakai
kembali dengan menambahkan opsi `-PreserveBuildCache`.

Hasilnya berada di:

```text
desktop_nuitka\build\launcher.dist\E-QuakeVis.exe
```

Distribusikan seluruh isi folder `launcher.dist`, bukan hanya file EXE. Python
tidak perlu dipasang pada komputer tujuan karena runtime sudah berada di folder
tersebut.

Untuk paket ZIP, ekstrak ke folder baru/kosong lalu jalankan
`E-QuakeVis.exe`. Jangan menimpa atau mencampur isinya dengan build lama,
karena DLL dan modul Python dalam satu paket harus berasal dari build yang sama.
Start pertama dapat memerlukan sekitar 20-30 detik sebelum browser terbuka.

Setelah standalone berhasil diuji, build satu file dapat dibuat dengan:

```powershell
.\desktop_nuitka\build.ps1 -Mode onefile -ConsoleMode disable -SkipInstall
```

## Penyimpanan data

Data referensi di folder `data` dan `Trench` dibundel sebagai data baca-saja.
Database yang ditambah pengguna disimpan permanen di:

```text
%LOCALAPPDATA%\E-QuakeVis\data\gempa_master.csv
%LOCALAPPDATA%\E-QuakeVis\data\gempa_working.sqlite
```

`gempa_master.csv` tetap menjadi arsip/format ekspor. Aplikasi membaca query
terindeks dari `gempa_working.sqlite`, yang sudah berisi hasil klasifikasi wilayah,
darat/laut, dan jarak sesar. Pada penggunaan pertama, keduanya disalin dari seed
di folder `data` yang dibundel. Mengganti atau memperbarui EXE tidak menghapus
data pengguna.

Saat pertama kali menjalankan versi bernama E-QuakeVis, data dari instalasi lama
di `%LOCALAPPDATA%\DatabaseInteraktif` disalin otomatis agar tetap tersedia.

Jika aplikasi gagal mulai pada komputer pengguna, log diagnostik tersedia di:

```text
%LOCALAPPDATA%\E-QuakeVis\runtime\startup.log
%LOCALAPPDATA%\E-QuakeVis\runtime\server.log
```

## Koneksi internet

EXE tidak mengubah kebutuhan jaringan aplikasi. Basemap dan aset web Folium
yang masih berasal dari layanan daring membutuhkan internet. Data gempa,
sesar, batas administrasi, trench, slab, dan kepadatan penduduk dibaca dari
aset lokal.
