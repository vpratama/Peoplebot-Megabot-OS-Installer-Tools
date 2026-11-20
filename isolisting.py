# Peoplebot-Megabot OS Installer Tools
# PXE Boot ISO Menu Generator - Legacy BIOS, Modern BIOS, UEFI BIOS, and UEFI Secure Boot

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer

# ── Load .env file ──────────────────────────────────────────────────────────────
def load_dotenv(env_path):
    """Simple .env parser that supports KEY=VALUE and KEY="VALUE" formats."""
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # Skip empty values so hardcoded defaults remain in effect
            if key and value:
                os.environ.setdefault(key, value)


load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ── Expand tilde in path ────────────────────────────────────────────────────────
def _expand(p):
    return os.path.expanduser(p) if p.startswith("~") else p


# ── Configuration (read from .env with fallback defaults) ──────────────────────
TFTP_DIR   = os.getenv("TFTP_DIR", os.path.expanduser("~/Downloads/tftp"))
TFTP_DIR   = _expand(TFTP_DIR)
ISO_DIR    = os.path.join(TFTP_DIR, os.getenv("ISO_DIR_NAME", "isos"))
EXTRACT_DIR = os.path.join(TFTP_DIR, os.getenv("EXTRACT_DIR_NAME", "extracted"))
CFG_DIR    = os.path.join(TFTP_DIR, os.getenv("CFG_DIR_NAME", "pxelinux.cfg"))
CFG_FILE   = os.path.join(CFG_DIR,   os.getenv("CFG_FILE_NAME", "default"))

# UEFI boot file download URLs (fallback mirrors)
UEFI_URL_IPXE  = os.getenv("UEFI_IPXE_URL",
    "https://mirrors.kernel.org/archlinux/extra/os/x86_64/ipxe-2.0.0-2-x86_64.pkg.tar.zst")
UEFI_URL_SHIM  = os.getenv("UEFI_SHIM_URL",
    "https://mirrors.kernel.org/archlinux/extra/os/x86_64/shim-16.1-1-any.pkg.tar.zst")
UEFI_URL_GRUB  = os.getenv("UEFI_GRUB_URL",
    "http://archive.ubuntu.com/ubuntu/pool/main/g/grub2-signed/grub-efi-amd64-signed_1.215+2.14-2ubuntu1_amd64.deb")
# Legacy BIOS bootloader URL

# Legacy BIOS bootloader URL

INTERFACE   = os.getenv("PXE_INTERFACE", "enp45s0")
SERVER_IP   = os.getenv("PXE_SERVER_IP", "192.168.254.2")
DHCP_START  = os.getenv("PXE_DHCP_START", "192.168.254.100")
DHCP_END    = os.getenv("PXE_DHCP_END", "192.168.254.200")
HTTP_PORT   = int(os.getenv("PXE_HTTP_PORT", "8085"))
MAX_MEMDISK_MB = int(os.getenv("PXE_MAX_MEMDISK_MB", "300"))

# ── Bundled dnsmasq (no host install required) ──────────────────────────────────
# Path to the bundled dnsmasq binary (dynamically linked against glibc, which
# every Linux host has). Dynamic linking is required for netlink interface
# enumeration — a static build cannot resolve interface names like "enp45s0".
# Override with DNSMASQ_BIN in .env if you want to use a different binary.
DNSMASQ_BIN = os.getenv("DNSMASQ_BIN", os.path.join(TFTP_DIR, "dnsmasq"))
DNSMASQ_BIN = _expand(DNSMASQ_BIN)
DNSMASQ_SRC_URL = os.getenv(
    "DNSMASQ_SRC_URL", "https://thekelleys.org.uk/dnsmasq/dnsmasq-2.93.tar.xz"
)

DNSMASQ_ARGS = [
    DNSMASQ_BIN,
    "-d",
    "-i",
    INTERFACE,
    "--bind-interfaces",
    f"--dhcp-range={DHCP_START},{DHCP_END},255.255.255.0,12h",
    "--dhcp-boot=pxelinux.0",
    f"--tftp-root={TFTP_DIR}",
    "--enable-tftp",
    # Keep all state inside the project folder — no writes to /var needed
    f"--dhcp-leasefile={os.path.join(TFTP_DIR, 'dnsmasq.leases')}",
    f"--pid-file={os.path.join(TFTP_DIR, 'dnsmasq.pid')}",
    "--port=0",
]

dnsmasq_proc = None


def _privilege_prefix(command):
    """Return the command prefixed with a privilege-elevation tool if needed."""
    if os.geteuid() == 0:
        return list(command)
    if shutil.which("pkexec"):
        return ["pkexec"] + list(command)
    for askpass in ("/usr/bin/ksshaskpass", "/usr/bin/zenity", "/usr/bin/ssh-askpass"):
        if os.path.exists(askpass):
            os.environ["SUDO_ASKPASS"] = askpass
            break
    return ["sudo", "-A"] + list(command)


def stop_existing_dnsmasq():
    """Terminate any dnsmasq process holding DHCP/TFTP ports.

    Uses privilege escalation so root-owned dnsmasq instances can be killed.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-x", "dnsmasq"],
            capture_output=True, text=True, check=False,
        )
        pids = [p for p in result.stdout.split() if p.strip()]
        if not pids:
            return
        print(f"Stopping {len(pids)} stale dnsmasq process(es)...")
        # pkexec/sudo needed because the conflicting processes are usually root-owned
        kill_prefix = _privilege_prefix(["kill", "-9"])
        # kill all in one shot (pass all PIDs)
        subprocess.run(
            kill_prefix + pids,
            capture_output=True, check=False,
        )
        time.sleep(0.8)
        # Verify none remain
        result = subprocess.run(
            ["pgrep", "-x", "dnsmasq"],
            capture_output=True, text=True, check=False,
        )
        survivors = []
        for p in result.stdout.split():
            p = p.strip()
            if not p:
                continue
            # Ignore zombie processes (already dead, just not reaped)
            try:
                with open(f"/proc/{p}/stat", encoding="utf-8") as fh:
                    state = fh.read().split(") ", 1)[1][0]
                if state != "Z":
                    survivors.append(p)
            except (OSError, IndexError):
                survivors.append(p)
        if survivors:
            print(f"  Warning: {len(survivors)} dnsmasq process(es) still running")
    except Exception as e:
        print(f"Warning: could not stop existing dnsmasq: {e}")


def start_dnsmasq_with_gui_auth():
    """Start the bundled dnsmasq via pkexec/sudo, prompting for admin rights."""
    global dnsmasq_proc, DNSMASQ_ARGS

    # Ensure the bundled binary exists (build it if needed)
    ensure_dnsmasq()
    DNSMASQ_ARGS[0] = DNSMASQ_BIN  # refresh in case ensure_dnsmasq changed it

    # Free DHCP/TFTP ports from any system dnsmasq
    stop_existing_dnsmasq()

    cmd = _privilege_prefix(DNSMASQ_ARGS)

    print(f"Starting dnsmasq: {DNSMASQ_BIN}")
    print("Requesting administrator privileges...")
    dnsmasq_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(2)

    if dnsmasq_proc.poll() is not None:
        output = dnsmasq_proc.stdout.read().strip()
        print("Failed to start dnsmasq (authentication canceled or port conflict).")
        if output:
            print(f"  dnsmasq: {output}")
        sys.exit(1)


def cleanup(signum=None, frame=None):
    """Stops the bundled dnsmasq cleanly when the Python script exits."""
    global dnsmasq_proc
    print("\nShutting down PXE server and dnsmasq...")
    if dnsmasq_proc and dnsmasq_proc.poll() is None:
        subprocess.run(
            ["pkexec", "kill", str(dnsmasq_proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    sys.exit(0)


def download_file(url, dest, member=None):
    """Download a file from URL to destination.

    If `member` is set and the URL points to a .tar.zst / .tar.gz / .tar.xz
    or .deb package, extract that member into `dest` instead of saving the
    raw download.
    """
    print(f"Downloading {os.path.basename(dest)} from {url}...")
    is_archive = url.endswith((".tar.zst", ".tar.xz", ".tar.gz", ".tgz", ".deb"))
    tmp = dest + ".download"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (PXE-Installer-Tools)"}
        )
        with urllib.request.urlopen(req, timeout=120) as response, \
             open(tmp, "wb") as out:
            shutil.copyfileobj(response, out)
        if is_archive and member:
            if url.endswith(".deb"):
                # .deb is an ar archive containing data.tar.{xz,zst,gz}
                ar_list = subprocess.run(
                    ["ar", "t", tmp], capture_output=True, text=True, check=True
                ).stdout.split()
                data_members = [m for m in ar_list if m.startswith("data.tar.")]
                if not data_members:
                    raise FileNotFoundError("data.tar.* inside .deb")
                data_member = data_members[0]
                tmpdir = dest + ".extract"
                shutil.rmtree(tmpdir, ignore_errors=True)
                os.makedirs(tmpdir, exist_ok=True)
                try:
                    data_tar = os.path.join(tmpdir, data_member)
                    with open(data_tar, "wb") as fh:
                        subprocess.run(
                            ["ar", "p", tmp, data_member], stdout=fh, check=True
                        )
                    subprocess.run(
                        ["tar", "-xf", data_tar, "-C", tmpdir, member], check=True
                    )
                    extracted = os.path.join(tmpdir, member.lstrip("./"))
                    shutil.copy2(extracted, dest)
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    os.remove(tmp)
            else:
                import tarfile
                tmpdir = dest + ".extract"
                shutil.rmtree(tmpdir, ignore_errors=True)
                os.makedirs(tmpdir, exist_ok=True)
                try:
                    with tarfile.open(tmp, "r:*") as tf:
                        matches = [m for m in tf.getmembers()
                                   if m.isfile() and m.name.endswith("/" + member)]
                        if not matches:
                            raise FileNotFoundError(member)
                        tf.extract(matches[0], tmpdir)
                    extracted = os.path.join(tmpdir, matches[0].name)
                    shutil.copy2(extracted, dest)
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    os.remove(tmp)
                except Exception:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    raise
        else:
            shutil.move(tmp, dest)

        size = os.path.getsize(dest)
        if size > 1024:
            print(f"  ✓ Downloaded ({size:,} bytes)")
            return True
        else:
            print(f"  ✗ File too small ({size} bytes), likely corrupted")
            os.remove(dest)
            return False
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return False


def ensure_dnsmasq():
    """Build the bundled dnsmasq from source if it is not present.

    Returns the path to a usable dnsmasq binary. Falls back to a host-installed
    dnsmasq if the build fails (missing compiler or no internet).
    """
    global DNSMASQ_BIN

    if os.path.isfile(DNSMASQ_BIN) and os.access(DNSMASQ_BIN, os.X_OK):
        return DNSMASQ_BIN

    print(f"Bundled dnsmasq not found at {DNSMASQ_BIN}")
    host_dnsmasq = shutil.which("dnsmasq")
    if not all([shutil.which("gcc"), shutil.which("make"), shutil.which("tar")]):
        if host_dnsmasq:
            print(f"  Build tools missing — falling back to host dnsmasq: {host_dnsmasq}")
            DNSMASQ_BIN = host_dnsmasq
            return DNSMASQ_BIN
        print("  ✗ Cannot build dnsmasq: gcc/make/tar not available.")
        return DNSMASQ_BIN

    print("  Building bundled dnsmasq from source (dynamic link for netlink support)...")
    tmpdir = os.path.join(TFTP_DIR, ".dnsmasq-build")
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
        os.makedirs(tmpdir, exist_ok=True)
        archive = os.path.join(tmpdir, "dnsmasq.tar.xz")
        if not download_file(DNSMASQ_SRC_URL, archive):
            raise RuntimeError("source download failed")

        subprocess.run(["tar", "-xf", archive, "-C", tmpdir], check=True)
        srcdirs = [d for d in os.listdir(tmpdir) if d.startswith("dnsmasq-")]
        if not srcdirs:
            raise RuntimeError("source archive has no dnsmasq directory")
        srcdir = os.path.join(tmpdir, srcdirs[0])

        # Dynamic build (needs only glibc, present on all Linux hosts).
        # A static build cannot enumerate interfaces via netlink.
        env = os.environ.copy()
        subprocess.run(
            ["make", "-j4", "CFLAGS=-Wall -O2"],
            cwd=srcdir, env=env, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        built = os.path.join(srcdir, "src", "dnsmasq")
        if not os.path.isfile(built):
            raise RuntimeError("build produced no binary")
        shutil.copy2(built, DNSMASQ_BIN)
        os.chmod(DNSMASQ_BIN, 0o755)
        print(f"  ✓ Built dnsmasq: {DNSMASQ_BIN}")
    except Exception as e:
        print(f"  ✗ Build failed: {e}")
        if host_dnsmasq:
            print(f"  Falling back to host dnsmasq: {host_dnsmasq}")
            DNSMASQ_BIN = host_dnsmasq
        else:
            print("  ✗ No usable dnsmasq available. DHCP/TFTP will not start.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return DNSMASQ_BIN


def ensure_boot_files():
    """Ensure all necessary PXE boot files are present, downloading if needed."""
    print(f"Boot directory: {TFTP_DIR}")

    # ── Legacy BIOS files ────────────────────────────────────────────────────
    legacy_files = ["pxelinux.0", "ldlinux.c32", "libutil.c32", "menu.c32", "memdisk"]
    legacy_missing = []
    for fname in legacy_files:
        path = os.path.join(TFTP_DIR, fname)
        if os.path.isfile(path) and os.path.getsize(path) > 1024:
            sz = os.path.getsize(path)
            print(f"  ✓ {fname} ({sz:,} bytes)")
        else:
            legacy_missing.append(fname)
            print(f"  ✗ {fname} NOT FOUND or invalid")

    if legacy_missing:
        print(f"\nMissing legacy BIOS files: {', '.join(legacy_missing)}")
        # Try to copy pxelinux.0 from system syslinux
        system_px = "/usr/lib/syslinux/bios/pxelinux.0"
        if os.path.isfile(system_px) and os.path.getsize(system_px) > 1024:
            import shutil
            shutil.copy2(system_px, os.path.join(TFTP_DIR, "pxelinux.0"))
            print(f"  ↓ Copied pxelinux.0 from system syslinux")
        else:
            print("Install from your distribution's package:")
            print("  Debian/Ubuntu: sudo apt install syslinux-common")
            print("  Fedora/RHEL:   sudo dnf install syslinux")
            print("  Arch:          sudo pacman -S syslinux")

    # ── iPXE / UEFI files (self-contained, working sources) ──────────────────
    # Direct URLs where available; Arch packages otherwise (with member path).
    # Format: filename -> (url, member_inside_archive or None)
    efi_map = {
        "undionly.kpxe": ("https://boot.ipxe.org/undionly.kpxe", None),
        "ipxe.pxe":      ("https://boot.ipxe.org/ipxe.pxe", None),
        "ipxe.efi":      (UEFI_URL_IPXE, "ipxe/x86_64/ipxe.efi"),
        "shimx64.efi":   (UEFI_URL_SHIM, "shim/shimx64.efi"),
        "mmx64.efi":     (UEFI_URL_SHIM, "shim/mmx64.efi"),
        "grubx64.efi":   (UEFI_URL_GRUB,
                          "./usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed"),
    }
    for fname, (url, member) in efi_map.items():
        path = os.path.join(TFTP_DIR, fname)
        if os.path.isfile(path) and os.path.getsize(path) > 1024:
            sz = os.path.getsize(path)
            print(f"  ✓ {fname} ({sz:,} bytes)")
        else:
            if os.path.exists(path):
                os.remove(path)
            print(f"  ↓ {fname} missing/invalid — downloading...")
            download_file(url, path, member=member)



def extract_iso_if_needed(iso_name):
    iso_path = os.path.join(ISO_DIR, iso_name)
    safe_name = os.path.splitext(iso_name)[0].replace(" ", "_")
    target_dir = os.path.join(EXTRACT_DIR, safe_name)
    marker = os.path.join(target_dir, ".done")

    if os.path.exists(marker):
        return safe_name

    os.makedirs(target_dir, exist_ok=True)
    print(f"Extracting boot files from {iso_name} for low-RAM PXE...")
    subprocess.run(
        ["bsdtar", "-xf", iso_path, "-C", target_dir],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    with open(marker, "w", encoding="utf-8") as f:
        f.write("ok")
    return safe_name


def detect_boot_params(safe_name):
    base_dir = os.path.join(EXTRACT_DIR, safe_name)
    kernel_candidates = []
    initrd_candidates = []
    squashfs_rel = None

    for root, _, files in os.walk(base_dir):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), TFTP_DIR)
            fl = f.lower()
            if fl.startswith("vmlinuz") or fl == "linux":
                kernel_candidates.append(rel)
            elif fl.startswith("initrd"):
                initrd_candidates.append(rel)
            elif fl == "filesystem.squashfs":
                squashfs_rel = rel

    # Skip bootloaders meant for other purposes (Xen domU, GTK installer,
    # package pools) and prefer the standard install/live directories.
    def pick(candidates):
        bad = ("/pool/", "/xen/", "/gtk/", "/efi/", "/boot/grub/")
        good = [c for c in candidates if not any(b in c.lower() for b in bad)]
        if not good:
            good = candidates
        preferred = ("live", "casper", "install.amd", "install.386")
        def rank(c):
            low = c.lower()
            for i, pref in enumerate(preferred):
                if "/" + pref + "/" in low:
                    return (i, len(c))
            return (len(preferred), len(c))
        return sorted(good, key=rank)[0] if good else None

    kernel_rel = pick(kernel_candidates)
    initrd_rel = pick(initrd_candidates)

    if not (kernel_rel and initrd_rel):
        return None

    if squashfs_rel:
        append = (
            f"initrd={initrd_rel} boot=live union=overlay config components"
            " quiet noswap nomodeset acpi=off"
            f" fetch=http://{SERVER_IP}:{HTTP_PORT}/{squashfs_rel}"
        )
        return kernel_rel, append

    return kernel_rel, f"initrd={initrd_rel} lowmem/low=1 acpi=off"


def generate_pxe_menu():
    isos = sorted(
        [f for f in os.listdir(ISO_DIR) if f.lower().endswith(".iso")]
    )

    # Generate legacy BIOS config (syslinux/pxelinux)
    legacy_lines = [
        "UI menu.c32",
        "PROMPT 0",
        "TIMEOUT 100",
        "",
        "MENU TITLE Peoplebot-Megabot OS Installer (Legacy BIOS)",
        "",
        "LABEL local",
        "    MENU LABEL Boot from Local Hard Drive",
        "    LOCALBOOT 0",
        "",
    ]

    for idx, iso in enumerate(isos):
        iso_path = os.path.join(ISO_DIR, iso)
        size_mb = os.path.getsize(iso_path) / (1024 * 1024)

        if size_mb <= MAX_MEMDISK_MB:
            legacy_lines.extend([
                f"LABEL iso_{idx}",
                f"    MENU LABEL {iso} (Memdisk - {int(size_mb)}MB)",
                "    KERNEL memdisk",
                f"    INITRD isos/{iso}",
                "    APPEND iso raw",
                "",
            ])
        else:
            safe_name = extract_iso_if_needed(iso)
            boot_info = detect_boot_params(safe_name)
            if boot_info:
                kernel_rel, append_args = boot_info
                legacy_lines.extend([
                    f"LABEL iso_{idx}",
                    f"    MENU LABEL {iso} (Low-RAM Extracted)",
                    f"    KERNEL {kernel_rel}",
                    f"    APPEND {append_args}",
                    "",
                ])

    legacy_lines.extend([
        "",
        "SEPARATOR",
        "",
        "DEFAULT local",
    ])

    # Write legacy BIOS config
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(legacy_lines))

    # Generate UEFI x86_64 config using iPXE
    uefi_lines = [
        "# iPXE UEFI Boot Menu for Peoplebot-Megabot OS Installer",
        "",
        "# Boot options: local, ISO memdisk, ISO extracted (Low-RAM)",
        "",
    ]

    # Local boot via UEFI
    uefi_lines.append("# Boot from local hard drive (UEFI)")
    uefi_lines.append("menu select")
    uefi_lines.append("item --key 1 \"Boot from Local Drive\"")
    uefi_lines.append("chain --sda1")
    uefi_lines.append("")

    # ISO boot via memdisk (small ISOs)
    uefi_lines.append("# Boot from small ISOs via memdisk (UEFI)")
    uefi_lines.append("item --key 2 \"Memdisk Boot\"")
    uefi_lines.append("chain --loaded1 || echo No memdisk loaded")
    uefi_lines.append("")

    # ISO extracted (Low-RAM) boot
    uefi_lines.append("# Boot from large ISOs - Low-RAM mode (UEFI)")
    uefi_lines.append("item --key 3 \"Low-RAM Extracted\"")
    uefi_lines.append("set booturl http://${server_ip}:${http_port}")
    uefi_lines.append("chain http://${server_ip}:${http_port}/boot/loader/entries/conf.lst")
    uefi_lines.append("")

    uefi_lines.append("# Press ESC to enter iPXE shell")
    uefi_lines.append("esc")
    uefi_lines.append("")

    with open(os.path.join(TFTP_DIR, "uefi_menu.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(uefi_lines))

    print(f"Generated legacy BIOS config and UEFI menu")


class ReuseAddrTCPServer(TCPServer):
    allow_reuse_address = True


def start_http_server():
    server = ReuseAddrTCPServer(("0.0.0.0", HTTP_PORT), QuietHandler)
    print(
        f"HTTP server streaming extracted boot files on port {HTTP_PORT}..."
    )
    server.serve_forever()


class QuietHandler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=TFTP_DIR, **kwargs)

    def log_message(self, format, *args):
        if args and "/health" in str(args):
            return
        super().log_message(format, *args)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # 1. Pop up password dialog and start dnsmasq BEFORE anything else
    start_dnsmasq_with_gui_auth()

    os.makedirs(ISO_DIR, exist_ok=True)
    os.makedirs(EXTRACT_DIR, exist_ok=True)
    os.makedirs(CFG_DIR, exist_ok=True)

    # 2. Ensure necessary boot files are present
    print("Checking and downloading necessary PXE boot files...")
    ensure_boot_files()

    # 3. Start HTTP server for squashfs streaming
    threading.Thread(target=start_http_server, daemon=True).start()

    # 4. Watch ISOs folder and update PXE menu
    last_isos = None
    print(f"Watching {ISO_DIR} for ISOs (Ctrl+C to stop all services)...")
    while True:
        if dnsmasq_proc.poll() is not None:
            print("dnsmasq stopped unexpectedly. Exiting.")
            break

        current_isos = sorted(
            [f for f in os.listdir(ISO_DIR) if f.lower().endswith(".iso")]
        )
        if current_isos != last_isos:
            generate_pxe_menu()
            last_isos = current_isos
        time.sleep(2)
