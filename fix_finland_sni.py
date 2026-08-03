import sys

file_path = 'c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix serverNames in generate_nodes_config
old_sni_block = '''"serverNames": [getattr(settings, "VLESS_SNI", "www.samsung.com")]'''
new_sni_block = '''"serverNames": [getattr(settings, "VLESS_SNI", "www.samsung.com"), "wikipedia.org", "yahoo.com"]'''

content = content.replace(old_sni_block, new_sni_block)

# Fix tg_free sni (since I hardcoded yahoo.com, let's just make sure it's yahoo.com or www.samsung.com, but since server now accepts yahoo.com, it will work. I will change it to www.samsung.com to be safe)
old_tg_free_sni = '''sni = "yahoo.com"'''
new_tg_free_sni = '''sni = getattr(settings, "VLESS_SNI", "www.samsung.com")'''
content = content.replace(old_tg_free_sni, new_tg_free_sni)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated serverNames in generate_nodes_config")
