import unittest
from unittest.mock import patch, MagicMock
import json
import deepseek_client
from ai_advisor_engine import generate_ai_advisor_analysis


class TestDeepSeekClient(unittest.TestCase):
    def test_model_candidate_list_default(self):
        candidates = deepseek_client.get_model_candidate_list()
        self.assertIn("deepseek-v4-flash", candidates)
        self.assertIn("deepseek-chat", candidates)
        self.assertNotIn("deepseek-v4-flash-0731", candidates)

    def test_model_candidate_list_custom_override(self):
        candidates = deepseek_client.get_model_candidate_list("deepseek-v4-pro")
        self.assertEqual(candidates[0], "deepseek-v4-pro")
        self.assertIn("deepseek-v4-flash", candidates)

    def test_clean_and_parse_json_direct(self):
        raw = '{"key": "value", "score": 100}'
        res = deepseek_client.clean_and_parse_json(raw)
        self.assertEqual(res, {"key": "value", "score": 100})

    def test_clean_and_parse_json_with_markdown_fences(self):
        raw = '```json\n{\n  "recommendation": "BUY",\n  "target": 50000\n}\n```'
        res = deepseek_client.clean_and_parse_json(raw)
        self.assertEqual(res["recommendation"], "BUY")
        self.assertEqual(res["target"], 50000)

    def test_clean_and_parse_json_with_surrounding_chatter(self):
        raw = 'Dưới đây là kết quả phân tích JSON:\n```json\n{"status": "ok", "passed": true}\n```\nChúc bạn thành công!'
        res = deepseek_client.clean_and_parse_json(raw)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["passed"])

    def test_clean_and_parse_json_invalid(self):
        with self.assertRaises(ValueError):
            deepseek_client.clean_and_parse_json("Đây không phải là JSON nào cả")

    @patch("deepseek_client.requests.post")
    @patch("deepseek_client.get_deepseek_api_key", return_value="sk-test-key-12345")
    def test_call_deepseek_json_success(self, mock_key, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"analysis": "Tích cực", "rating": 5}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        mock_post.return_value = mock_response

        res = deepseek_client.call_deepseek_json(
            messages=[{"role": "user", "content": "Phân tích SSI"}],
            enable_thinking=False,
        )
        self.assertEqual(res["analysis"], "Tích cực")
        self.assertEqual(res["rating"], 5)
        self.assertIn("_deepseek_meta", res)
        self.assertEqual(res["_deepseek_meta"]["total_tokens"], 150)

        # Verify payload contains thinking: disabled
        called_kwargs = mock_post.call_args[1]
        self.assertEqual(called_kwargs["json"]["thinking"], {"type": "disabled"})

    @patch("deepseek_client.requests.post")
    @patch("deepseek_client.get_deepseek_api_key", return_value="sk-test-key-12345")
    def test_call_deepseek_json_model_fallback(self, mock_key, mock_post):
        # 1st call fails with 400 (e.g. invalid model), 2nd succeeds with 200
        bad_response = MagicMock()
        bad_response.status_code = 400
        bad_response.text = '{"error": "Model not supported"}'

        good_response = MagicMock()
        good_response.status_code = 200
        good_response.json.return_value = {
            "choices": [{"message": {"content": '{"fallback_success": true}'}}],
            "usage": {"total_tokens": 80},
        }
        mock_post.side_effect = [bad_response, good_response]

        res = deepseek_client.call_deepseek_json(
            messages=[{"role": "user", "content": "Test fallback"}]
        )
        self.assertTrue(res["fallback_success"])
        self.assertEqual(mock_post.call_count, 2)


class TestAiAdvisorEngineIntegration(unittest.TestCase):
    def test_generate_ai_advisor_analysis_fallback_without_price(self):
        with self.assertRaises(ValueError):
            generate_ai_advisor_analysis("DUMMY", {"current_price": 0.0})


if __name__ == "__main__":
    unittest.main()
