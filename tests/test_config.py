import unittest
from config import AppConfig


class TestAppConfig(unittest.TestCase):
    def test_defaults_when_env_is_empty(self) -> None:
        cfg = AppConfig.from_env({})
        self.assertEqual(cfg.keyword_provider, "dataforseo")
        self.assertEqual(cfg.request_timeout_seconds, 12)
        self.assertEqual(cfg.max_retries, 2)
        self.assertTrue(cfg.respect_robots_txt)
        self.assertFalse(cfg.has_keyword_credentials)
        self.assertFalse(cfg.has_slack_webhook)
        self.assertFalse(cfg.using_postgres)

    def test_dataforseo_credentials_detected(self) -> None:
        cfg = AppConfig.from_env(
            {
                "KEYWORD_PROVIDER": "dataforseo",
                "DATAFORSEO_LOGIN": "user@example.com",
                "DATAFORSEO_PASSWORD": "secret_api_password",
            }
        )
        self.assertTrue(cfg.has_keyword_credentials)

    def test_keywords_everywhere_credentials_detected(self) -> None:
        cfg = AppConfig.from_env(
            {
                "KEYWORD_PROVIDER": "keywords_everywhere",
                "KEYWORDS_EVERYWHERE_API_KEY": "ke_live_12345",
            }
        )
        self.assertEqual(cfg.keyword_provider, "keywords_everywhere")
        self.assertTrue(cfg.has_keyword_credentials)

    def test_slack_webhook_validation(self) -> None:
        invalid_cfg = AppConfig.from_env({"SLACK_WEBHOOK_URL": "https://example.com/webhook"})
        self.assertFalse(invalid_cfg.has_slack_webhook)

        valid_cfg = AppConfig.from_env(
            {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T000/B000/XXXX"}
        )
        self.assertTrue(valid_cfg.has_slack_webhook)

    def test_bounds_clamping_and_invalid_ints(self) -> None:
        cfg = AppConfig.from_env(
            {
                "REQUEST_TIMEOUT_SECONDS": "not_a_number",
                "MAX_RETRIES": "999",
                "RESPECT_ROBOTS_TXT": "false",
            }
        )
        self.assertEqual(cfg.request_timeout_seconds, 12)
        self.assertEqual(cfg.max_retries, 5)
        self.assertFalse(cfg.respect_robots_txt)


if __name__ == "__main__":
    unittest.main()
