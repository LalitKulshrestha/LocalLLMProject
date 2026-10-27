from pathlib import Path
import unittest

from localforge.engines import _facefusion_progress, _replace_tokens, _split_progress_messages
from localforge.formats import generation_size


class AppTests(unittest.TestCase):
    def test_recursive_tokens(self):
        value = {"a": ["{{PROMPT}}", {"seed": "{{SEED}}"}]}
        self.assertEqual(_replace_tokens(value, {"{{PROMPT}}": "hello", "{{SEED}}": 42}), {
            "a": ["hello", {"seed": 42}]
        })

    def test_expected_workflow_exists(self):
        self.assertTrue(Path("workflows/text_to_image.json").exists())

    def test_video_platform_presets_have_exact_aspect_ratios(self):
        portrait_width, portrait_height, portrait_ratio = generation_size("text_to_video", "shorts")
        landscape_width, landscape_height, landscape_ratio = generation_size("image_to_video", "youtube")
        self.assertEqual((portrait_width, portrait_height, portrait_ratio), (576, 1024, "9:16"))
        self.assertEqual((landscape_width, landscape_height, landscape_ratio), (1024, 576, "16:9"))
        self.assertEqual(portrait_width * 16, portrait_height * 9)
        self.assertEqual(landscape_width * 9, landscape_height * 16)

    def test_invalid_video_format_is_rejected(self):
        with self.assertRaises(ValueError):
            generation_size("text_to_video", "square")

    def test_facefusion_carriage_return_progress(self):
        lines, pending = _split_progress_messages("", "extracting: 50%\rprocessing: 12%\rprocessing:")
        self.assertEqual(lines[:2], ["extracting: 50%", "processing: 12%"])
        self.assertEqual(pending, "processing:")
        lines, pending = _split_progress_messages(pending, " 13%\r")
        self.assertEqual(lines[0], "processing: 13%")
        self.assertEqual(pending, "")
        stage, fraction = _facefusion_progress("processing: 13%")
        self.assertEqual(stage, "Swapping faces")
        self.assertAlmostEqual(fraction, 0.204)


if __name__ == "__main__":
    unittest.main()
