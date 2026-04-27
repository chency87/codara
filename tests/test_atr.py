from amesh.core.atr import ATRModule


def test_atr_extracts_exact_search_replace_action():
    output = """### src/app.py
<<<<<<< SEARCH
old_value = 1
=======
old_value = 2
>>>>>>> REPLACE
"""
    actions = ATRModule().extract_actions(output)

    assert actions == [
        {
            "action_id": "atr_1",
            "type": "patch",
            "format": "search_replace",
            "path": "src/app.py",
            "file": "src/app.py",
            "search": "old_value = 1",
            "replace": "old_value = 2",
            "raw": "<<<<<<< SEARCH\nold_value = 1\n=======\nold_value = 2\n>>>>>>> REPLACE",
            "source": "text",
            "exact": True,
        }
    ]


def test_atr_extracts_json_write_action():
    output = """```json
{
  "actions": [
    {
      "type": "write_file",
      "path": "src/new_module.py",
      "content": "print('hello')\\n"
    }
  ]
}
```"""
    actions = ATRModule().extract_actions(output)

    assert actions == [
        {
            "action_id": "atr_1",
            "type": "write_file",
            "path": "src/new_module.py",
            "file": "src/new_module.py",
            "source": "json",
            "raw": {
                "type": "write_file",
                "path": "src/new_module.py",
                "content": "print('hello')\n",
            },
            "format": "write_file",
            "content": "print('hello')\n",
            "exact": True,
        }
    ]


def test_atr_extracts_unified_diff_action():
    output = """```diff
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-old
+new
```"""
    actions = ATRModule().extract_actions(output)

    assert actions == [
        {
            "action_id": "atr_1",
            "type": "patch",
            "format": "unified_diff",
            "patch": "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new",
            "paths": ["src/app.py"],
            "path": "src/app.py",
            "file": "src/app.py",
            "raw": "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new",
            "source": "diff",
            "exact": True,
        }
    ]
