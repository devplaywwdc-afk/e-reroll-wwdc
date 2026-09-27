import re

with open('backup_clean/libUE4.so', 'rb') as f:
    data = f.read()

# Search for LANGSTAT or strings near language selection
print("Searching for language strings...")
for pat in [rb'LANGUAGE_[A-Z]+', rb'LANG_[A-Z]+', rb'LANGSTAT_[A-Z]+']:
    matches = set(re.findall(pat, data))
    if matches:
        print(f"{pat}: {matches}")

# Also find strings that look like "ENGLISH", "JAPANESE", etc.
langs = [b"ENGLISH", b"JAPANESE", b"THAI", b"CHINESE", b"KOREAN", b"SPANISH", b"FRENCH", b"GERMAN", b"ITALIAN"]
found = {}
for lang in langs:
    count = data.count(lang)
    found[lang.decode()] = count
print("Occurrences of common language words:", found)
