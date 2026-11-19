import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("supplier", Path(__file__).resolve().parents[1] / "supplier-import/index.py")
supplier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supplier)
URL = "https://shop.example/item/oven"


class SupplierTest(unittest.TestCase):
    def test_structured_product(self):
        html = '''<script type="application/ld+json">{"@graph":[
          {"@type":"Product","name":"Oven","image":{"url":"https://img.example/media?id=2"},
           "description":"<p>Line one</p><p>Line two</p>","offers":{"@id":"#offer"},
           "additionalProperty":{"name":"Power","value":4}},
          {"@type":"Offer","@id":"#offer","price":320,"priceCurrency":"EUR"}]}</script>'''
        data = supplier.parse_generic(html, URL)
        self.assertEqual((data["price"], data["currency"]), ("320", "EUR"))
        self.assertEqual(data["images"], ["https://img.example/media?id=2"])
        self.assertEqual(data["specsText"], "Power: 4")
        self.assertEqual(data["description"], "Line one\nLine two")

    def test_visible_card_without_price(self):
        html = '''<h1>Печь</h1><section class="product-description">Описание</section>
          <div class="gallery"><img data-src="/media/oven"></div>
          <table><tr><th>Вес</th><td>10 кг</td></tr></table>
          <div class="related"><span class="price">99 ₽</span></div>'''
        data = supplier.parse_generic(html, URL)
        self.assertEqual(data["price"], "")
        self.assertEqual(data["images"], ["https://shop.example/media/oven"])
        self.assertEqual(data["specsText"], "Вес: 10 кг")

    def test_category_is_not_a_product(self):
        with self.assertRaises(ValueError):
            supplier.parse_generic("<h1>Каталог</h1><p>Все товары</p>", URL)

    def test_private_destination_rejected_before_connect(self):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))]), \
                patch("socket.create_connection") as connect, self.assertRaises(ValueError):
            supplier.fetch(URL, 1000)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
