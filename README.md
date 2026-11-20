# Peoplebot-Megabot OS Installer Tools

## Cara Mengakses Repository

```bash
git clone git@github.com:vpratama/Peoplebot-Megabot-OS-Installer-Tools.git
```

<p align="center">
  <img src="https://github.com/vpratama/Peoplebot-Megabot-OS-Installer-Tools/blob/main/logo.bmp?raw=true" alt="Peoplebot-Megabot"/>
</p>


## Ringkasan

Proyek ini menyediakan infrastruktur lengkap **TFTP/PXE boot** untuk instalasi dan recovery sistem Peoplebot dan Megabot melalui jaringan. Mendukung semua jenis firmware:

- **Legacy BIOS** — via syslinux/pxelinux
- **Modern BIOS** — via iPXE
- **UEFI BIOS** — via iPXE UEFI
- **UEFI Secure Boot** — via shim + iPXE

Skrip `isolisting.py` secara otomatis memindai folder `isos/`, menghasilkan menu boot, menjalankan server DHCP/TFTP/HTTP, dan mengunduh file boot yang diperlukan jika belum ada.

---

## Struktur Folder

```
tftp/
├── isolisting.py           # Skrip utama: generator menu + server PXE
├── .env                    # Konfigurasi aktif (nilai nyata)
├── .env.example            # Template konfigurasi (placeholder)
├── .gitignore              # Aturan git ignore proyek
│
├── isos/                   # File ISO untuk PXE boot
│   └── .gitignore
│
├── extracted/              # Hasil ekstraksi ISO besar untuk mode Low-RAM
│   └── .gitignore
│
├── pxelinux.cfg/
│   └── default             # Menu boot Legacy BIOS (dihasilkan otomatis)
│
├── uefi_menu.txt           # Menu iPXE untuk UEFI (dihasilkan otomatis)
├── ![logo.bmp](logo.bmp)   # Logo boot menu Legacy BIOS (MENU BACKGROUND)
│
├── dnsmasq                 # Binary dnsmasq (bundled, tidak perlu install)
├── dnsmasq.leases          # File lease DHCP (dibuat saat runtime)
│
└── File boot PXE:
    ├── pxelinux.0          # Bootloader Legacy BIOS
    ├── ldlinux.c32
    ├── libutil.c32
    ├── menu.c32
    ├── memdisk             # RAM disk untuk ISO kecil
    ├── ipxe.pxe            # iPXE Legacy BIOS
    ├── ipxe-legacy.pxe
    ├── undionly.kpxe       # iPXE undionly (Legacy)
    ├── ipxe.efi            # iPXE UEFI
    ├── shimx64.efi         # UEFI Secure Boot shim
    ├── mmx64.efi           # MOK manager (Secure Boot)
    └── grubx64.efi         # GRUB UEFI (signed, untuk chain boot alternatif)
```

---

## Cara Kerja

### 1. Deteksi ISO & Menu Boot

`isolisting.py` membaca semua file `.iso` di folder `isos/` lalu menentukan metode boot:

| Ukuran ISO | Metode Boot | Keterangan |
|------------|-------------|------------|
| ≤ `PXE_MAX_MEMDISK_MB` (default 300 MB) | **Memdisk** | ISO dimuat penuh ke RAM |
| > `PXE_MAX_MEMDISK_MB` | **Low-RAM Extracted** | ISO diekstrak, kernel/initrd dimuat via HTTP |

### 2. Menu yang Dihasilkan

- **`pxelinux.cfg/default`** — menu untuk Legacy BIOS (format standar syslinux)
- **`uefi_menu.txt`** — menu iPXE untuk UEFI/Secure Boot

Keduanya diperbarui otomatis setiap kali ada perubahan file ISO di folder `isos/`.

### 3. Server yang Dijalankan

| Server | Fungsi | Port |
|--------|--------|------|
| **dnsmasq** (bundled) | DHCP + TFTP | UDP 67 / 69 |
| **HTTP Server** (Python) | Streaming file squashfs untuk mode Low-RAM | TCP 8085 (dapat diubah) |

---

## Instalasi & Persiapan

### 1. Salin file konfigurasi

```bash
cp .env.example .env
nano .env
```

Sesuaikan nilai berikut:

- `TFTP_DIR` — path absolut folder proyek ini
- `PXE_INTERFACE` — nama interface jaringan (cek dengan `ip link`)
- `PXE_SERVER_IP` — IP statis server di jaringan PXE
- `PXE_DHCP_START` / `PXE_DHCP_END` — rentang IP DHCP
- `PXE_MAX_MEMDISK_MB` — batas ukuran ISO untuk boot via memdisk

### 2. Pastikan interface memiliki IP statis

Server PXE **harus** memiliki alamat IP statis pada interface yang digunakan (sesuai `PXE_SERVER_IP`). Contoh:

```bash
sudo ip addr add 192.168.254.2/24 dev enp45s0
sudo ip link set enp45s0 up
```

### 3. Letakkan file ISO

Tempatkan file `.iso` di dalam folder `isos/`. File ISO tidak akan ter-commit ke git (sudah diabaikan oleh `.gitignore`).

### 4. Jalankan skrip

```bash
python isolisting.py
```

Skrip akan:

1. Meminta hak administrator (pkexec/sudo) untuk menjalankan dnsmasq
2. Menghentikan proses dnsmasq lama yang bentrok
3. Memeriksa dan mengunduh file boot jika belum ada
4. Menjalankan HTTP server untuk streaming squashfs
5. Memantau folder `isos/` dan memperbarui menu secara otomatis

Tekan **Ctrl+C** untuk menghentikan semua layanan dengan bersih.

---

## Konfigurasi (.env)

| Variabel | Default | Deskripsi |
|----------|---------|-----------|
| `TFTP_DIR` | `/home/user/tftp` | Folder dasar proyek |
| `ISO_DIR_NAME` | `isos` | Folder file ISO |
| `EXTRACT_DIR_NAME` | `extracted` | Folder hasil ekstraksi |
| `CFG_DIR_NAME` | `pxelinux.cfg` | Folder konfigurasi PXE |
| `CFG_FILE_NAME` | `default` | Nama file menu PXE |
| `UEFI_IPXE_URL` | Arch Linux ipxe package | Sumber unduh `ipxe.efi` |
| `UEFI_SHIM_URL` | Arch Linux shim package | Sumber unduh `shimx64.efi` |
| `UEFI_GRUB_URL` | Ubuntu signed grub package | Sumber unduh `grubx64.efi` (file `.deb`) |
| `PXE_INTERFACE` | `enp45s0` | Interface jaringan untuk dnsmasq |
| `PXE_SERVER_IP` | `192.168.254.2` | IP server TFTP/HTTP |
| `PXE_DHCP_START` | `192.168.254.100` | Awal rentang DHCP |
| `PXE_DHCP_END` | `192.168.254.200` | Akhir rentang DHCP |
| `PXE_HTTP_PORT` | `8085` | Port HTTP streaming |
| `PXE_MAX_MEMDISK_MB` | `300` | Batas ukuran ISO untuk memdisk |
| `DNSMASQ_BIN` | *(kosong)* | Path binary dnsmasq (kosong = gunakan bundled) |
| `DNSMASQ_SRC_URL` | thekelleys.org.uk | Sumber source dnsmasq jika perlu build ulang |

---

## Dukungan Boot per BIOS

| Jenis BIOS | File yang Dibutuhkan | Keterangan |
|------------|----------------------|------------|
| **Legacy BIOS** | `pxelinux.0`, `menu.c32`, `memdisk`, `logo.bmp` | Boot via syslinux |
| **Modern BIOS** | `ipxe.pxe`, `undionly.kpxe` | Boot via iPXE |
| **UEFI BIOS** | `ipxe.efi` | Boot via iPXE UEFI |
| **UEFI Secure Boot** | `shimx64.efi` + `mmx64.efi` + `ipxe.efi` + `grubx64.efi` | shim → iPXE / shim → GRUB |

> Semua file boot di atas sudah tersedia di folder proyek dan akan diunduh otomatis jika hilang.

---

## File Boot Bundled

Proyek ini menyertakan binary dnsmasq sehingga **tidak perlu menginstal dnsmasq di sistem host**. Binary ini di-build secara dinamis (hanya membutuhkan glibc yang ada di semua distribusi Linux) agar dapat mengenali nama interface via netlink.

Jika binary hilang, skrip akan otomatis:

1. Mengunduh source dnsmasq dari `DNSMASQ_SRC_URL`
2. Melakukan build dengan `gcc` dan `make`
3. Jika build gagal, menggunakan dnsmasq dari sistem (jika tersedia)

---

## Pemecahan Masalah

### "dnsmasq stopped unexpectedly"

Penyebab umum:

- Interface belum memiliki IP statis → tambahkan IP ke interface (lihat langkah 2)
- Port 67/69 masih dipakai proces lain → skrip akan menghentikan dnsmasq lama otomatis
- Hak akses ditolak → pastikan pkexec/sudo berfungsi

### "Address already in use" pada port HTTP

Port `PXE_HTTP_PORT` (8085) sedang digunakan. Ubah nilainya di `.env` atau hentikan proses yang memakainya:

```bash
ss -tlnp | grep 8085
```

### File EFI berukuran 0 byte

Hapus file tersebut, lalu jalankan ulang skrip — file akan diunduh ulang otomatis:

```bash
rm -f ipxe.efi shimx64.efi mmx64.efi grubx64.efi
python isolisting.py
```

---

## Lisensi & Catatan

Proyek ini dibuat untuk kebutuhan deployment sistem Peoplebot dan Megabot. File ISO dan komponen boot (syslinux, iPXE, shim, dnsmasq) tunduk pada lisensi masing-masing.

---

## Lisensi & Catatan

Proyek ini dibuat untuk kebutuhan deployment sistem Peoplebot dan Megabot. File ISO dan komponen boot (syslinux, iPXE, shim, dnsmasq) tunduk pada lisensi masing-masing.

## Platform Support

### Linux (Native)
Proyek ini dirancang untuk dijalankan secara nativa di sistem Linux. Semua file dan binary yang diperlukan sudah termasuk di proyek:

- **Menyukai distribusi Linux** berpusat pada Arch/Ubuntu/Debian-based
- **Binary dnsmasq** sudah bundling (tidak perlu install eksternal)
- **Python script** `isolisting.py` berjalan dengan Python 3 standar
- **TFTP/HTTP server** melalui Python built-in
- **Konfigurasi network** melalui ip command (seperti `ip link`, `ip addr`)

**Untuk menjalankan di Linux:**
```bash
# Pastikan Python 3 tersedia
python --version

# Jalankan skrip
python isolisting.py
```

### Windows (via WSL2)
Proyek ini dapat dijalankan di Windows menggunakan **Windows Subsystem for Linux (WSL2)**. Ini adalah cara terbaik untuk menjalankan proyek ini di Windows:

**Persyaratan:**
- Windows 10/11 dengan WSL2 didukung
- Akses administrator untuk mengkonfigurasi jaringan

**Cara Menginstal:**
```powershell
# Aktifkan WSL jika belum
wsl --install

# Atau install dnsmasq jika perlu di dalam WSL
# di PowerShell sebagai Admin:
wsl --update

# Clone/Download proyek ke folder WSL-accessible location
# Contoh: di dalam folder home di WSL
git clone https://github/your-repo/peoplebot-pxe-installer.git
cd peoplebot-pxe-installer

# Jalankan skrip melalui WSL
./isolisting.py
```

**Catatan Penting untuk Windows/WSL:**
- Konfigurasi IP statis perlu di-set di dalam WSL session, bukan dari PowerShell
- Gunakan `ip addr add ...` dan `ip link set ...` di dalam WSL terminal
- Port forwarding mungkin diperlukan jika mengakses dari luar WSL
- File ISO letakkan di folder yang dapat diakses oleh both Windows dan WSL (seperti `\\wsl$\distro\home\user\tftp\isos`)

### Perbedaan Utama Antara Platform:

| Fitur | Linux (Native) | Windows (WSL2) |
|-------|---------------|----------------|
| **Binary dnsmasq** | Sudah termasuk, langsung jalan | Sudah termasuk, jalan di WSL |
| **Konfigurasi jaringan** | `ip` command langsung | `ip` command di WSL terminal |
| **Port akses** | Langsung dari host | Perlu cek IP WSL (`ip addr show eth0`) |
| **File ISO** | Path absolut langsung | Gunakan `\\wsl$\` path atau folder ter-share |
| **Pengelolaan layanan** | `Ctrl+C` menghentikan semuanya | `Ctrl+C` di WSL terminal |

### Menjalankan di Dua Platform Secara Bersamaan:
1. Simpan folder proyek di location yang diakses WSL (seperti `~/tftp/` di Ubuntu di WSL)
2. Pastikan file ISO juga diakses dari location yang sama
3. Jalankan `python isolisting.py` di WSL terminal
4. Akses boot menu dari klien PXE di jaringan lokal

3. Jalankan `python isolisting.py` di WSL terminal
4. Akses boot menu dari klien PXE di jaringan lokal

---

## Spesifikasi & Requirement

### Requirements for Running This Project

#### General Requirements
- **Python 3.8+** - Required to run `isolisting.py` script
- **dnsmasq binary** - Bundled within project (no host installation required)
- **Disk Space** - Approximately 2GB free space for ISO files and extracted content
- **Network Interface** - Static IP recommended on PXE server interface

#### Linux (Native) Requirements
- **Operating System**: Linux distribution (Arch, Ubuntu, Debian, CentOS, etc.)
- **Python**: `python3` package installed
- **IP Command**: `iproute2` package for network configuration (`ip link`, `ip addr`)
- **Permissions**: Root/sudo access for dnsmasq and network configuration
- **Dependencies**: None additional (bundled dnsmasq is statically linked)

**Installation on Linux:**
```bash
# Debian/Ubuntu-based
sudo apt update
sudo apt install -y python3 iproute2

# Arch-based
sudo pacman -S --needed python3 iproute2

# Verification
python3 --version
ip --version
```

#### Windows (via WSL2) Requirements
- **Windows 10/11** with WSL2 support enabled
- **WSL2 Kernel** - Must be running WSL2 (not WSL1)
- **Python 3** - Available inside WSL distribution
- **Network Access** - Internet connection for downloading boot files if needed

**Installation on Windows (WSL2):**
```powershell
# Enable WSL if not already enabled
wsl --install

# Restart computer after installation

# Inside WSL terminal (Ubuntu):
sudo apt update
sudo apt install -y iproute2

# Verification inside WSL
python3 --version
ip --version
```

#### File ISO Requirements
- **Format**: `.iso` files compatible with PXE boot
- **Size**: 
  - ≤ 300 MB: Boot via Memdisk (loaded into RAM)
  - > 300 MB: Boot via Low-RAM Extracted mode (HTTP streaming)
- **Minimum**: 1 ISO file required for boot menu to display options

#### Boot Menu Logo
- **logo.bmp** - Boot menu background image (included in project)
- Placed in project root directory
- Configured via `MENU BACKGROUND logo.bmp` in `pxelinux.cfg/default`

---

*Peoplebot-Megabot OS Installer Tools — Solusi Instalasi Sistem Melalui Jaringan*
