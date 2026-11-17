"""
Download files from the PubChem FTP site (served over HTTPS) with resume support and md5 verification.
"""
import hashlib
import os
import re
import time
import urllib.error
import urllib.request

from pubchem_local import __version__

USER_AGENT = "pubchem-local/%s" % __version__
CHUNK = 1 << 20


class DownloadError(Exception):
    pass


def _request(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=dict({"User-Agent": USER_AGENT}, **(headers or {})))
    return urllib.request.urlopen(req, timeout=timeout)


def _retry(func, attempts=6, log=None):
    delay = 5
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except (urllib.error.URLError, OSError, DownloadError) as err:
            if attempt == attempts:
                raise
            if log:
                log("Download problem (%s), retrying in %ds (attempt %d/%d)" % (err, delay, attempt, attempts))
            time.sleep(delay)
            delay = min(delay * 2, 300)


def list_directory(url, pattern):
    """
    List file names in an FTP directory listing (HTML index) matching a regular expression.
    """
    def _get():
        with _request(url.rstrip("/") + "/") as resp:
            return resp.read().decode("utf-8", errors="replace")
    html = _retry(_get)
    names = sorted(set(re.findall(r'href="([^"/?]+)"', html)))
    rx = re.compile(pattern)
    return [n for n in names if rx.fullmatch(n)]


def fetch_md5(url):
    def _get():
        with _request(url + ".md5") as resp:
            return resp.read().decode("ascii", errors="replace").split()[0].strip().lower()
    try:
        return _retry(_get, attempts=3)
    except Exception:
        return None


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK * 8), b""):
            h.update(block)
    return h.hexdigest()


def is_current(dest, remote_md5):
    """True if 'dest' exists and was verified against the same remote md5 earlier."""
    marker = str(dest) + ".md5"
    if not os.path.exists(dest) or not os.path.exists(marker) or remote_md5 is None:
        return False
    with open(marker) as fh:
        return fh.read().strip() == remote_md5


def download(url, dest, log=None, progress=None, verify_md5=True):
    """
    Download 'url' to 'dest'. Partial downloads ('dest.part') are resumed. If the file is already present and its
    md5 matches the remote '.md5' file, nothing is downloaded.

    :param progress: optional callable(bytes_done, bytes_total)
    :return: path to the downloaded file
    """
    dest = str(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    remote_md5 = fetch_md5(url) if verify_md5 else None

    if is_current(dest, remote_md5):
        return dest
    if os.path.exists(dest) and remote_md5 is None and not verify_md5:
        return dest

    part = dest + ".part"

    def _get():
        offset = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": "bytes=%d-" % offset} if offset else {}
        try:
            resp = _request(url, headers=headers, timeout=120)
        except urllib.error.HTTPError as err:
            if err.code == 416:  # range not satisfiable: we already have everything
                return
            raise
        with resp:
            if offset and resp.status != 206:
                offset = 0  # server ignored the range request: start over
            total = resp.headers.get("Content-Length")
            total = int(total) + offset if total is not None else None
            with open(part, "ab" if offset else "wb") as fh:
                done = offset
                for block in iter(lambda: resp.read(CHUNK), b""):
                    fh.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
            if total is not None and done < total:
                raise DownloadError("connection closed early (%d of %d bytes)" % (done, total))

    _retry(_get, log=log)

    if remote_md5 is not None:
        actual = file_md5(part)
        if actual != remote_md5:
            os.remove(part)
            raise DownloadError("md5 mismatch for %s (expected %s, got %s)" % (url, remote_md5, actual))
    os.replace(part, dest)
    if remote_md5 is not None:
        with open(dest + ".md5", "w") as fh:
            fh.write(remote_md5)
    return dest
