"""Yandex Cloud Functions entry point: index.handler."""
import base64
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

ORIGIN = "https://apisareva22-droid.github.io"
PHOTO_PATH = re.compile(r"\.(?:jpeg|jpg|png|webp)$", re.I)


def supplier_url(url):
    if not isinstance(url, str) or len(url) > 8192 or re.search(r"[\\\x00-\x1f\x7f]", url):
        raise ValueError("Некорректная ссылка поставщика.")
    parts = urlsplit(url)
    if (parts.scheme not in {"http", "https"} or parts.username is not None
            or not parts.hostname or "." not in parts.hostname or
            parts.port not in {None, 443 if parts.scheme == "https" else 80}):
        raise ValueError("Укажите публичную ссылку HTTP или HTTPS на страницу товара.")
    return url


def fetch(url, limit):
    deadline = time.monotonic() + 11
    for _ in range(5):
        parts = urlsplit(supplier_url(url))
        host = parts.hostname.encode("idna").decode("ascii")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        records = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        addresses = [ipaddress.ip_address(record[4][0]) for record in records]
        if not addresses or any(not ip.is_global or ip.is_multicast for ip in addresses):
            raise ValueError("Доступны только публичные сайты поставщиков.")
        # Connect to the checked address, retaining the DNS name for TLS and the Host header.
        timeout = min(6, deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError()
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.sock = socket.create_connection((str(addresses[0]), port), timeout)
            if parts.scheme == "https":
                conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=host)
            target = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parts.query:
                target += "?" + quote(parts.query, safe="/?%:@!$&'()*+,;=-._~")
            conn.request("GET", target, headers={"Host": host, "User-Agent": "Alfaprom-KP-Importer/2.0",
                                                  "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308} and response.getheader("Location"):
                url = urljoin(url, response.getheader("Location"))
                continue
            if response.status != 200:
                raise ValueError(f"Сайт поставщика ответил HTTP {response.status}. Проверьте доступность карточки.")
            content = response.read(limit + 1)
            if len(content) > limit:
                raise ValueError("Файл поставщика превышает допустимый размер.")
            return content, response.headers, url
        finally:
            conn.close()
    raise ValueError("Слишком много перенаправлений на сайте поставщика.")


class ProductPage(HTMLParser):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.section = ""
        self.ignore = ""
        self.heading = None
        self.name = ""
        self.parts = {key: [] for key in ("offer", "description", "specsText")}
        self.images = []
        self.image_names = set()
        self.buffer = []

    def flush(self):
        value = " ".join("".join(self.buffer).split())
        self.buffer.clear()
        if value and self.section in self.parts:
            self.parts[self.section].append(value)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self.ignore = tag
        if self.ignore:
            return
        if tag in {"h1", "h2", "h3"}:
            self.flush()
            self.heading = []
        if tag in {"div", "p", "tr", "td", "th", "li", "br", "dt", "dd", "h4"}:
            self.flush()
        if self.section == "offer" and tag in {"a", "img"}:
            candidates = [attrs.get(k) for k in ("href", "data-large", "data-src", "src")]
            for raw in filter(None, candidates):
                try:
                    image_url = supplier_url(urljoin(self.url, raw))
                    path = urlsplit(image_url).path
                    if "/iblock/" not in path or not PHOTO_PATH.search(path):
                        continue
                except ValueError:
                    continue
                # The gallery repeats each original as WebP and as a thumbnail.
                filename = urlsplit(image_url).path.rsplit("/", 1)[-1].removesuffix(".webp")
                if filename not in self.image_names:
                    self.image_names.add(filename)
                    self.images.append(image_url)
                break

    def handle_data(self, value):
        if self.ignore:
            return
        (self.heading if self.heading is not None else self.buffer).append(value)

    def handle_endtag(self, tag):
        if self.ignore:
            if tag == self.ignore:
                self.ignore = ""
            return
        if tag in {"h1", "h2", "h3"} and self.heading is not None:
            heading = " ".join("".join(self.heading).split())
            self.heading = None
            if tag == "h1":
                self.name, self.section = heading, "offer"
            elif self.name and heading != self.name:
                self.section = {"описание товара": "description",
                                "характеристики товара": "specsText"}.get(heading.lower(), "")
        if tag in {"div", "p", "tr", "td", "th", "li", "dt", "dd", "h4"}:
            self.flush()

    def result(self):
        self.flush()
        if not self.name or not any(self.parts[k] for k in ("description", "specsText")):
            raise ValueError("По ссылке не удалось распознать карточку оборудования.")
        price = re.search(r"Стоимость\s+([\d][\d\s]*(?:[,.]\d{1,2})?)\s*₽",
                          " ".join(self.parts["offer"]), re.I)
        specs = self.parts["specsText"]
        return dict(productName=self.name, description="\n".join(
            x for x in self.parts["description"] if x != "Показать полностью"),
            specsText="\n".join(f"{k}: {v}" for k, v in zip(specs[::2], specs[1::2])),
            price=re.sub(r"\s+", "", price[1]).replace(",", ".") if price else "",
            currency="RUB", images=self.images[:24])


class Element:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def text(self):
        if self.tag in {"script", "style", "noscript", "svg"}:
            return ""
        if self.tag == "br":
            return "\n"
        chunks = [c if isinstance(c, str) else c.text() for c in self.children]
        result = "".join(chunks)
        return result + "\n" if self.tag in {"div", "p", "li", "tr", "dd"} else result


class GenericPage(HTMLParser):
    void = set("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self):
        super().__init__()
        self.stack = [Element("root", [])]
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        if len(self.nodes) > 80000 or len(self.stack) > 250:
            raise ValueError("Страница поставщика слишком сложна для импорта.")
        if tag in {"li", "p", "tr", "td", "th", "dt", "dd"} and tag == self.stack[-1].tag:
            self.stack.pop()
        node = Element(tag, attrs)
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in self.void:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def clean(value):
    if not isinstance(value, (str, float, int)):
        return ""
    lines = (" ".join(line.split()) for line in str(value).splitlines())
    return "\n".join(filter(None, lines)).strip()


def plain_description(value):
    value = clean(value)
    if re.search(r"<[a-zA-Z][^>]*>", value):
        fragment = GenericPage()
        fragment.feed(value)
        return clean(fragment.stack[0].text())
    return unescape(value)


def items(value):
    return value if isinstance(value, list) else [value] if value is not None else []


def parse_generic(source, url):
    page = GenericPage()
    page.feed(source)
    page.close()
    meta, props, products, graph = {}, {}, [], {}
    headings, descriptions, images, rows, visible_prices = [], [], [], [], []
    gallery = False

    def walk(node, in_gallery=False):
        nonlocal gallery
        a, tag = node.attrs, node.tag
        label = (str(a.get("id", "")) + " " + str(a.get("class", ""))).lower()
        if tag in {"nav", "footer", "aside", "style", "noscript", "svg", "template"} or re.search(
                r"recommend|related|upsell|cross-sell|also-bought|viewed-products", label):
            return
        if tag == "script":
            if str(a.get("type", "")).split(";")[0].strip().lower() == "application/ld+json":
                try:
                    queue = items(json.loads("".join(node.children).strip().lstrip("\ufeff")))
                    while queue:
                        obj = queue.pop(0)
                        if not isinstance(obj, dict):
                            continue
                        if isinstance(obj.get("@id"), str):
                            graph[obj["@id"]] = obj
                        if any(str(t).rsplit("/", 1)[-1] == "Product" for t in items(obj.get("@type"))):
                            products.append(obj)
                        queue.extend(items(obj.get("@graph")) + items(obj.get("mainEntity")))
                except (ValueError, RecursionError):
                    pass
            return
        if tag == "meta":
            key = a.get("property") or a.get("name")
            if key and a.get("content"):
                meta.setdefault(key.lower(), a["content"])
        if tag == "h1":
            headings.append(clean(node.text()))
        if tag in {"span", "div", "strong", "b", "p"} and "price" in label and not re.search(
                r"old|previous|cross|installment|credit|monthly|wholesale", label):
            if not any(isinstance(c, Element) and c.tag in {"del", "s", "div"} for c in node.children):
                price_text = clean(node.text())
                match = re.fullmatch(r"(?:Цена\s*:?\s*)?(\d[\d\s]*(?:[.,]\d{1,2})?)\s*(₽|руб\.?|RUB|USD|EUR|€)", price_text, re.I)
                if match:
                    visible_prices.append((match[1], match[2].upper()))
        for prop in (a.get("itemprop") or "").split():
            if prop in {"image", "price", "priceCurrency", "description"}:
                val = a.get("content") or a.get("href") or a.get("data-src") or a.get("src") or clean(node.text())
                if val:
                    props.setdefault(prop, []).append(val)
        if tag in {"div", "section", "article", "p"} and re.search(
                r"(?:product|item|detail|tab)[\w-]*(?:descr|opisanie)|(?:^|\s)(?:description|opisanie)(?:\s|$)", label):
            descriptions.append(clean(node.text()))
        if tag == "tr":
            cells = [clean(c.text()) for c in node.children if isinstance(c, Element) and c.tag in {"td", "th"}]
            if len(cells) == 2 and all(cells):
                rows.append(": ".join(cells))
        if tag == "dl":
            key = ""
            for c in node.children:
                if isinstance(c, Element) and c.tag == "dt":
                    key = clean(c.text())
                elif isinstance(c, Element) and c.tag == "dd" and key:
                    rows.append(key + ": " + clean(c.text()))
                    key = ""
        in_gallery = in_gallery or bool(re.search(r"gallery|product[\w-]*(?:photo|image)|detail[\w-]*picture", label))
        if in_gallery and tag in {"a", "img"}:
            raw = a.get("data-zoom-image") or a.get("data-large") or a.get("data-src") or a.get("src")
            if tag == "a" and PHOTO_PATH.search((a.get("href") or "").split("?")[0]):
                raw = a["href"]
            if raw:
                gallery = True
                images.append(raw)
        for c in node.children:
            if isinstance(c, Element):
                walk(c, in_gallery)

    walk(page.stack[0])
    h1 = next(filter(None, headings), "")
    product = next((p for p in products if clean(p.get("name")).casefold() == h1.casefold()), None)
    if product is None:
        product = products[0] if len(products) == 1 else {}
    if len(products) > 1 and not product:
        raise ValueError("На странице несколько товаров. Вставьте ссылку на конкретное оборудование.")
    def first(prop):
        return next(iter(props.get(prop, [])), "")

    def resolve(obj):
        return graph.get(obj.get("@id"), obj) if isinstance(obj, dict) and len(obj) == 1 else obj

    def market(obj):
        if not isinstance(obj, dict):
            return "", ""
        return clean(obj.get("price")), clean(obj.get("priceCurrency"))

    amount, currency = "", ""
    offers = [resolve(o) for o in items(product.get("offers"))]
    if len(offers) == 1 and isinstance(offers[0], dict):
        amount, currency = market(offers[0])
        if not amount:
            amount, currency = market(resolve(offers[0].get("priceSpecification")))
    amount = amount or first("price") or meta.get("product:price:amount", "") or meta.get("og:price:amount", "")
    currency = currency or first("priceCurrency") or meta.get("product:price:currency", "") or meta.get("og:price:currency", "")
    if not amount and len(set(visible_prices)) == 1:
        amount, currency = visible_prices[0]
        currency = {"₽": "RUB", "РУБ": "RUB", "РУБ.": "RUB", "€": "EUR"}.get(currency, currency)
    description = plain_description(product.get("description") or first("description") or
                                    next(filter(None, descriptions), "") or meta.get("og:description") or meta.get("description"))
    name = clean(product.get("name") or h1 or meta.get("og:title"))
    if not name or not (product or amount or meta.get("og:type", "").startswith("product") or
                        (h1 and (rows or gallery) and (description or images))):
        raise ValueError("Не удалось распознать карточку. Проверьте, что ссылка ведёт на открытый товар.")
    specs = []
    for entry in items(product.get("additionalProperty")):
        entry = resolve(entry)
        if isinstance(entry, dict) and clean(entry.get("name")) and clean(entry.get("value")):
            specs.append(clean(entry["name"]) + ": " + clean(entry["value"]))
    photos = []
    structured_images = items(product.get("image")) or props.get("image", [])
    for entry in (structured_images if len(structured_images) > 1 else images + structured_images) or [meta.get("og:image")]:
        entry = resolve(entry)
        if isinstance(entry, dict):
            entry = entry.get("contentUrl") or entry.get("url")
        if isinstance(entry, str) and entry.strip():
            try:
                absolute = supplier_url(urljoin(url, entry.strip()))
            except ValueError:
                continue
            if absolute not in photos:
                photos.append(absolute)
    amount = re.sub(r"\s", "", str(amount)).replace(",", ".")
    return dict(productName=name, description=description[:100000],
                specsText="\n".join(dict.fromkeys(specs or rows))[:60000],
                price=amount if re.fullmatch(r"\d+(?:\.\d+)?", amount) else "",
                currency=currency.strip().upper(), images=photos[:24])


def handler(event, context):
    headers = {"Access-Control-Allow-Origin": ORIGIN, "Access-Control-Allow-Methods": "GET, OPTIONS",
               "Content-Type": "application/json; charset=utf-8"}
    def reply(status, value):
        return {"statusCode": status, "headers": headers, "body": json.dumps(value, ensure_ascii=False)}
    if event.get("httpMethod") == "OPTIONS":
        return reply(200, {})
    if event.get("httpMethod") != "GET":
        return reply(405, {"error": "Требуется GET."})
    try:
        args = event.get("queryStringParameters") or {}
        image = args.get("type") == "image"
        url = supplier_url(args.get("url", ""))
        data, meta, url = fetch(url, 2_000_000 if image else 3_000_000)
        if image:
            mime = meta.get_content_type()
            valid = {"image/jpeg": data.startswith(b"\xff\xd8\xff"), "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
                     "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP"}
            if not valid.get(mime):
                raise ValueError("Формат изображения не поддерживается.")
            return {**reply(200, {}), "headers": {**headers, "Content-Type": mime},
                    "isBase64Encoded": True, "body": base64.b64encode(data).decode("ascii")}
        if meta.get_content_type() not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("По ссылке ожидается HTML-страница товара.")
        declared = re.search(rb"""<meta\b[^>]*charset\s*=\s*["']?([\w-]+)""", data[:8192], re.I)
        encoding = meta.get_content_charset() or (declared[1].decode("ascii") if declared else "utf-8")
        try:
            source = data.decode(encoding, errors="replace")
        except (LookupError, ValueError):
            source = data.decode("utf-8", errors="replace")
        if urlsplit(url).hostname in {"hmru.ru", "www.hmru.ru"}:
            parser = ProductPage(url)
            parser.feed(source)
            parser.close()
            try:
                return reply(200, parser.result())
            except ValueError:
                pass
        return reply(200, parse_generic(source, url))
    except ValueError as exc:
        return reply(400, {"error": str(exc)})
    except (http.client.HTTPException, OSError):
        return reply(502, {"error": "Поставщик не ответил. Попробуйте позже."})

