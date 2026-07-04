import re
html_path = r'c:\code\clasp\clasp\ui\static\index.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

# Fix toggle track globally by finding the span inside the toggle
html = re.sub(
    r'<span class="absolute top-0\.5 left-0\.5 w-3\.5 h-3\.5 bg-black transition-transform"></span>',
    r'<span class="absolute top-0.5 left-0.5 w-[14px] h-[14px] bg-black transition-all duration-200" :class="{ \'translate-x-6 bg-[#00e676]\': $el.previousElementSibling.checked }"></span>',
    html
)

# Fix chevron
html = html.replace('class="chevron" :class="{ open: prov._open }"', 'class="transition-transform duration-200" :class="{ \'rotate-180\': prov._open }"')

# Fix provider note
html = html.replace('class="provider-note"', 'class="text-sm text-neutral-600 mt-1"')

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)
