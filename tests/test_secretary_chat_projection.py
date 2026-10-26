import unittest

from universal_userio.voice.secretary_chat_projection import secretary_message_text


class SecretaryChatProjectionTests(unittest.TestCase):
    def test_userio_body_is_exposed_as_secretary_text(self):
        self.assertEqual("Ответ из тестовой группы", secretary_message_text({
            "body": "Ответ из тестовой группы",
        }))

    def test_legacy_text_field_remains_supported(self):
        self.assertEqual("старый формат", secretary_message_text({
            "text": "старый формат",
        }))

    def test_non_text_payload_does_not_leak_as_message_text(self):
        self.assertEqual("", secretary_message_text({"body": {"unexpected": True}}))


if __name__ == "__main__":
    unittest.main()
