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

    def test_full_description_and_embedded_specs(self):
        html = '''<h1>Запайщик</h1><meta itemprop="description" content="Запайщик">
          <meta itemprop="price" content="100"><div itemtype="https://schema.org/ImageObject">
          <meta itemprop="description" content="Фото аппарата"></div>
          <div itemprop="description">Ручной аппарат.<br>Размер картонной коробки 670х330х300 мм.<br>
          Потребляемая мощность аппарата: 1000 Ватт, электропитание: однофазное от сети напряжением 220 В.<br>
          Вес 15 кг. Тефлоновое покрытие, размер нагревательного элемента 273 х 200 мм<br>
          Гарантийный срок на предлагаемый аппарат составляет 3 года, включая обслуживание.</div>'''
        data = supplier.parse_generic(html, URL)
        self.assertTrue(data["description"].startswith("Ручной аппарат.\n"))
        self.assertIn("включая обслуживание.", data["description"])
        self.assertEqual(data["specsText"].splitlines(), [
            "Размер картонной коробки: 670х330х300 мм", "Потребляемая мощность аппарата: 1000 Ватт",
            "Электропитание: однофазное от сети напряжением 220 В", "Вес: 15 кг",
            "Размер нагревательного элемента: 273 х 200 мм", "Гарантийный срок: 3 года"])
        table = '<table><tr><td>Масса нетто</td><td>14 кг</td></tr></table>'
        self.assertEqual(supplier.parse_generic(html + table, URL)["specsText"], "Масса нетто: 14 кг")

    def test_private_destination_rejected_before_connect(self):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))]), \
                patch("socket.create_connection") as connect, self.assertRaises(ValueError):
            supplier.fetch(URL, 1000)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
