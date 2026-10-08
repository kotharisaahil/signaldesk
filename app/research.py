"""Bounded public-website research, with robots rules and pinned public IPs."""
import http.client
import ipaddress
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = "SignalDesk/2.0 (+local lead research; respects robots.txt)"
MAX_BYTES = 700000
MAX_TEXT = 12000


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("Use a valid company website URL.")
    value = value.strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError:
        raise ValueError("Invalid website port.")
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or port not in (None, 443) or any(ord(c) < 33 for c in value)):
        raise ValueError("Use a public HTTPS website without credentials or a custom port.")
    host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Private and local website addresses are not supported.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            raise ValueError("Only public website addresses are allowed.")
        host = "[{}]".format(host) if address.version == 6 else host
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def public_ip(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(item[4][0] for item in addresses))
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("The website resolves to a private or reserved address.")
    return ips[0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Use the checked address, while keeping the hostname for TLS certificate validation.
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def fetch(url, deadline=None, allowed=None):
    deadline = deadline or time.monotonic() + 30
    current = clean_url(url)
    original_host = urlsplit(current).hostname.removeprefix("www.")
    for _ in range(4):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Website research reached its time limit.")
        parts = urlsplit(current)
        if allowed is not None and not allowed(current):
            raise ValueError("Robots rules prevent reading the destination page.")
        if parts.hostname.removeprefix("www.") != original_host:
            raise ValueError("The website redirected to a different domain.")
        conn = PinnedHTTPS(parts.hostname, public_ip(parts.hostname), min(8, remaining))
        try:
            path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept":"text/html,text/plain", "Accept-Encoding":"identity"})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Website redirect has no destination.")
                current = clean_url(urljoin(current, location))
                continue
            mime = response.getheader("Content-Type", "").lower()
            chunks, size = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError("Website research reached its time limit.")
                chunk = response.read1(min(32768, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Website page is too large; paste the relevant text instead.")
            raw = b"".join(chunks)
            charset = re.search(r"charset=([\w-]+)", mime)
            encoding = charset.group(1) if charset else "utf-8"
            try:
                body = raw.decode(encoding, errors="replace")
            except LookupError:
                body = raw.decode("utf-8", errors="replace")
            return current, response.status, mime, body
        finally:
            conn.close()
    raise ValueError("Too many website redirects.")


class ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts, self.links, self.title = [], [], []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())
            if self.in_title:
                self.title.append(data.strip())


def research(website, fetcher=fetch):
    """Fetch the supplied page and up to two relevant same-site links. No search API."""
    if not website:
        return [], []
    root = clean_url(website)
    parts = urlsplit(root)
    origin = "https://" + parts.netloc
    deadline = time.monotonic() + 60
    warnings, sources = [], []
    robots = RobotFileParser()
    try:
        _, status, _, body = fetcher(origin + "/robots.txt", deadline)
        if status in (401, 403):
            return [], ["This website does not permit automated research."]
        if status == 404:
            robots.parse([])
        elif status == 200:
            robots.parse(body.splitlines())
        else:
            return [], ["Could not verify the site's robots rules. Website research was skipped."]
    except (OSError, ValueError, http.client.HTTPException):
        return [], ["Could not verify the site's robots rules. Paste website evidence into the inquiry if needed."]
    delay = robots.crawl_delay("SignalDesk") or robots.crawl_delay("*") or 0
    rate = robots.request_rate("SignalDesk") or robots.request_rate("*")
    if rate and rate.requests:
        delay = max(delay, rate.seconds / rate.requests)
    if delay > 5:
        return [], ["This site's crawl delay exceeds the bounded research window; paste relevant evidence instead."]
    queue, seen = [root], set()
    while queue and len(seen) < 3:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if not robots.can_fetch("SignalDesk", url):
            warnings.append("Robots rules prevent reading " + url)
            continue
        try:
            if delay:
                time.sleep(delay)
            final, status, mime, body = fetcher(url, deadline, allowed=lambda destination: robots.can_fetch("SignalDesk", destination))
            if status != 200 or not any(t in mime for t in ("text/html", "text/plain")):
                warnings.append("Could not read {} (HTTP {}).".format(url, status))
                continue
            parser = ReadableHTML()
            if "text/html" in mime:
                parser.feed(body)
                content = " ".join(parser.parts)[:MAX_TEXT]
            else:
                content = " ".join(body.split())[:MAX_TEXT]
            if len(content) < 100:
                warnings.append("Not enough readable text at " + url)
                continue
            sources.append({"id":"web" + str(len(sources) + 1), "kind":"website", "url":final,
                            "title":" ".join(parser.title)[:150] or urlsplit(final).hostname,
                            "text":content, "retrieved_at":timestamp()})
            for href in parser.links:
                candidate = urljoin(final, href)
                p = urlsplit(candidate)
                if (p.scheme == "https" and p.netloc == urlsplit(final).netloc
                        and not p.query and not p.fragment
                        and any(word in p.path.lower() for word in ("about", "product", "solution", "pricing", "company"))):
                    candidate = clean_url(candidate)
                    if candidate not in seen and candidate not in queue and len(queue) < 6:
                        queue.append(candidate)
        except (OSError, ValueError, http.client.HTTPException):
            warnings.append("Could not read " + url + ". The site may block automated access or require JavaScript.")
    return sources, warnings
