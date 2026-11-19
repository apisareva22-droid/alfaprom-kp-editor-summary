"""Yandex Cloud Functions entry point: index.handler."""
import base64
import json
import re
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ORIGIN = "https://apisareva22-droid.github.io"
PHOTO_PATH = re.compile(r"\.(?:jpeg|jpg|png|webp)$", re.I)


def supplier_url(url, image=False):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc.lower() not in {"hmru.ru", "www.hmru.ru"}
            or not parts.path.startswith("/upload/" if image else "/catalog/")
            or any(part in {".", ".."} for part in parts.path.split("/"))
            or re.search(r"[%\\\x00-\x20]", parts.path)
            or (image and not PHOTO_PATH.search(parts.path))):
        raise ValueError("Поддерживаются ссылки на товары каталога hmru.ru.")
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def fetch(url, limit):
    request = Request(url, headers={"User-Agent": "Alfaprom-KP-Importer/1.0"})
    with build_opener(NoRedirect()).open(request, timeout=7) as response:
        content = response.read(limit + 1)
        if len(content) > limit:
            raise ValueError("Файл поставщика превышает допустимый размер.")
        return content, response.headers


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
                    image_url = supplier_url(urljoin(self.url, raw), image=True)
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
            images=self.images[:24])


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
        url = supplier_url(args.get("url", ""), image)
        data, meta = fetch(url, 2_000_000 if image else 3_000_000)
        if image:
            mime = meta.get_content_type()
            if mime not in {"image/jpeg", "image/png", "image/webp"}:
                raise ValueError("Формат изображения не поддерживается.")
            return {**reply(200, {}), "headers": {**headers, "Content-Type": mime},
                    "isBase64Encoded": True, "body": base64.b64encode(data).decode("ascii")}
        parser = ProductPage(url)
        parser.feed(data.decode(meta.get_content_charset() or "utf-8", errors="replace"))
        parser.close()
        return reply(200, parser.result())
    except ValueError as exc:
        return reply(400, {"error": str(exc)})
    except (URLError, OSError):
        return reply(502, {"error": "Поставщик не ответил. Попробуйте позже."})

