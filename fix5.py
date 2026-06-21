with open("tests/unit/test_selector.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace('"nim"', '"nvidia_nim"')

with open("tests/unit/test_selector.py", "w", encoding="utf-8") as f:
    f.write(content)
