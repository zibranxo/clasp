import re
import os

html_path = r"c:\code\clasp\clasp\ui\static\index.html"
with open(html_path, "r", encoding="utf-8") as f:
    html = f.read()

# 1. Add Tailwind CDN and Config to <head>
tailwind_script = """
  <!-- Tailwind CSS -->
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            neo: {
              bg: "#fcf6e6",
              yellow: "#fde047",
              green: "#00e676",
              pink: "#ff2a85",
              cyan: "#00f0ff",
              purple: "#9d4edd",
              orange: "#ff6d00",
              dark: "#000000",
              light: "#ffffff",
              gray: "#e5e7eb",
            },
          },
          fontFamily: {
            display: ["'Space Grotesk'", "sans-serif"],
            body: ["'Lexend'", "sans-serif"],
            mono: ["'JetBrains Mono'", "monospace"],
          }
        }
      }
    }
  </script>
"""

# Insert tailwind script before closing head
html = html.replace("</head>", tailwind_script + "</head>")

# 2. Layout & Shell
html = html.replace('class="app-shell"', 'class="min-h-screen bg-[#fcf6e6] text-black font-body flex flex-col md:flex-row"')
html = html.replace('class="app-header"', 'class="fixed top-0 left-0 right-0 h-16 border-b-[3px] border-black bg-white flex items-center justify-between px-6 z-50 shadow-[0_4px_0px_0px_#000]"')
html = html.replace('class="logo"', 'class="font-display font-extrabold text-2xl tracking-tighter flex items-center gap-2 hover:scale-105 transition-transform"')
html = html.replace('class="logo-glyph"', 'class="text-[#ff2a85]"')
html = html.replace('class="header-version"', 'class="px-2 py-0.5 text-xs font-bold uppercase border-2 border-black bg-[#00f0ff]"')
html = html.replace('class="status-ring-wrap"', 'class="flex items-center gap-2 px-3 py-1 border-[3px] border-black bg-[#fde047] font-display font-bold text-xs uppercase shadow-[2px_2px_0px_0px_#000]"')
html = html.replace('class="header-clock"', 'class="font-mono text-sm font-bold ml-4"')
html = html.replace('class="header-spacer"', 'class="flex-1"')
html = html.replace('class="unsaved-bar"', 'class="flex items-center gap-4 px-4 py-2 bg-[#ff2a85] border-[3px] border-black shadow-[4px_4px_0px_0px_#000] text-white font-display font-bold fixed top-20 right-8 z-50"')

# Sidebar
html = html.replace('class="app-body"', 'class="flex-1 mt-16 md:ml-[260px] p-8 max-w-[1400px] w-full mx-auto"')
html = html.replace('class="sidebar"', 'class="hidden md:flex flex-col w-[260px] border-r-[3px] border-black bg-white h-[calc(100vh-4rem)] mt-16 fixed left-0 top-0 z-40 overflow-y-auto"')
html = html.replace('class="sidebar-top"', 'class="flex flex-col p-4 gap-2"')

# Nav Item
nav_item_re = re.compile(r'class="nav-item"(.*?)\s*:class="([^"]+)"', re.DOTALL)
html = nav_item_re.sub(r'class="w-full text-left px-4 py-3 font-display font-bold uppercase tracking-wide border-[3px] border-transparent hover:border-black hover:bg-[#fde047] hover:shadow-[4px_4px_0px_0px_#000] hover:-translate-y-1 transition-all flex items-center gap-3"\1 :class="{ \'border-black bg-[#00f0ff] shadow-[4px_4px_0px_0px_#000] -translate-y-1\': panel === item.id }"', html)
html = html.replace('class="nav-icon-wrap"', 'class="w-5 h-5"')
html = html.replace('class="nav-label"', 'class=""')
html = html.replace('class="sidebar-stats"', 'class="mt-auto p-4 border-t-[3px] border-black bg-[#fcf6e6] text-xs font-mono font-bold flex flex-col gap-3"')

# Typography
html = html.replace('class="panel-heading"', 'class="mb-8"')
html = html.replace('class="panel-title"', 'class="font-display font-black uppercase text-4xl mb-2 tracking-tighter"')
html = html.replace('class="panel-sub"', 'class="font-body text-lg font-medium text-neutral-800"')
html = html.replace('class="section-title"', 'class="font-display font-extrabold uppercase text-2xl mb-4 tracking-tight border-b-2 border-black pb-2 inline-block"')

# Base Buttons
btn_base = 'font-display font-bold uppercase border-[3px] border-black transition-all duration-150 select-none active:translate-x-[2px] active:translate-y-[2px] active:shadow-none'
btn_ghost = f'{btn_base} bg-white text-black px-4 py-2 text-sm shadow-[4px_4px_0px_0px_#000] hover:shadow-[8px_8px_0px_0px_#000] hover:-translate-x-[4px] hover:-translate-y-[4px] hover:bg-[#00f0ff]'
btn_save = f'{btn_base} bg-[#00e676] text-black px-6 py-2 text-sm shadow-[4px_4px_0px_0px_#000] hover:shadow-[8px_8px_0px_0px_#000] hover:-translate-x-[4px] hover:-translate-y-[4px]'
btn_add = f'{btn_base} bg-[#fde047] text-black px-4 py-1.5 text-xs shadow-[2px_2px_0px_0px_#000] hover:shadow-[4px_4px_0px_0px_#000] hover:-translate-x-[2px] hover:-translate-y-[2px]'
btn_test = f'{btn_base} bg-[#00f0ff] text-black px-4 py-1.5 text-xs shadow-[2px_2px_0px_0px_#000] hover:shadow-[4px_4px_0px_0px_#000] hover:-translate-x-[2px] hover:-translate-y-[2px] disabled:opacity-50 disabled:cursor-not-allowed'
btn_danger = f'{btn_base} bg-[#ff2a85] text-white px-4 py-1.5 text-xs shadow-[2px_2px_0px_0px_#ff2a85] hover:shadow-[4px_4px_0px_0px_#ff2a85] hover:-translate-x-[2px] hover:-translate-y-[2px]'

html = html.replace('class="btn-ghost"', f'class="{btn_ghost}"')
html = html.replace('class="btn-ghost magnetic"', f'class="{btn_ghost}"')
html = html.replace('class="btn-save magnetic"', f'class="{btn_save}"')
html = html.replace('class="btn-save"', f'class="{btn_save}"')
html = html.replace('class="btn-add-key"', f'class="{btn_add}"')
html = html.replace('class="btn-test"', f'class="{btn_test}"')
html = html.replace('class="btn-remove-key"', f'class="{btn_danger}"')
html = html.replace('class="btn-danger"', f'class="{btn_danger}"')
html = html.replace('class="btn-chain-remove"', f'class="{btn_danger}"')

# Badges
badge_base = 'inline-flex items-center gap-1.5 px-3 py-1 text-xs font-display font-extrabold uppercase border-[3px] border-black tracking-wide select-none shadow-[2px_2px_0px_0px_#000]'
html = html.replace("'badge-healthy': providerStatusText(name) === 'HEALTHY',", f"'bg-[#00e676] text-black': providerStatusText(name) === 'HEALTHY',")
html = html.replace("'badge-warning': providerStatusText(name) === 'SOFT_LIMIT',", f"'bg-[#fde047] text-black': providerStatusText(name) === 'SOFT_LIMIT',")
html = html.replace("'badge-cooling': providerStatusText(name) === 'COOLING_DOWN',", f"'bg-[#00f0ff] text-black': providerStatusText(name) === 'COOLING_DOWN',")
html = html.replace("'badge-error':   providerStatusText(name) === 'CIRCUIT_OPEN',", f"'bg-[#ff2a85] text-white': providerStatusText(name) === 'CIRCUIT_OPEN',")
html = html.replace("'badge-off':     providerStatusText(name) === 'OFF' || providerStatusText(name) === 'UNKNOWN'", f"'bg-neutral-300 text-black': providerStatusText(name) === 'OFF' || providerStatusText(name) === 'UNKNOWN'")
html = html.replace('class="badge"', f'class="{badge_base}"')

# Cards and Glass
card_base = 'border-[3px] border-black bg-white shadow-[4px_4px_0px_0px_#000] p-6 mb-6 hover:shadow-[8px_8px_0px_0px_#000] hover:-translate-x-1 hover:-translate-y-1 transition-all'
html = html.replace('class="glass provider-card tilt-card"', f'class="{card_base}"')
html = html.replace('class="glass stat-card glass-premium tilt-card"', f'class="{card_base} bg-[#fde047]"')
html = html.replace('class="glass stat-card tilt-card"', f'class="{card_base}"')
html = html.replace('class="glass system-bar"', f'class="border-[3px] border-black bg-white shadow-[4px_4px_0px_0px_#000] px-6 py-4 flex items-center gap-6 overflow-x-auto whitespace-nowrap mb-8 font-mono text-sm font-bold"')
html = html.replace('class="glass tier-table"', f'class="border-[3px] border-black bg-white shadow-[4px_4px_0px_0px_#000] overflow-hidden flex flex-col mb-8"')
html = html.replace('class="glass strategy-card flex items-center gap-4"', f'class="border-[3px] border-black bg-white shadow-[4px_4px_0px_0px_#000] p-4 flex items-center gap-4 cursor-pointer hover:bg-[#fcf6e6] transition-colors"')
html = html.replace('class="glass"', f'class="border-[3px] border-black bg-white shadow-[4px_4px_0px_0px_#000] p-6 mb-6"')

# Inputs
input_base = 'w-full px-4 py-3 font-mono text-sm font-bold text-black placeholder-neutral-400 bg-[#fcf6e6] border-[3px] border-black shadow-[2px_2px_0px_0px_#000] focus:shadow-[4px_4px_0px_0px_#000] focus:-translate-x-[2px] focus:-translate-y-[2px] transition-all outline-none'
html = html.replace('class="input-glass mono"', f'class="{input_base}"')
html = html.replace('class="input-glass"', f'class="{input_base}"')

# Misc Structural
html = html.replace('class="provider-header"', 'class="flex items-center justify-between cursor-pointer mb-2 gap-4 border-b-[3px] border-black pb-4 mb-4"')
html = html.replace('class="provider-name"', 'class="font-display font-black uppercase text-2xl tracking-tighter"')
html = html.replace('class="key-row"', 'class="flex items-center gap-3 w-full"')
html = html.replace('class="key-input-wrap"', 'class="flex-1 relative flex items-center"')
html = html.replace('class="key-reveal-btn"', 'class="absolute right-3 p-1 hover:bg-black/10 rounded"')
html = html.replace('class="grid-4"', 'class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6"')
html = html.replace('class="grid-2"', 'class="grid grid-cols-1 sm:grid-cols-2 gap-6"')
html = html.replace('class="grid-auto-3"', 'class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6"')
html = html.replace('class="stat-num hero"', 'class="font-display font-black text-6xl tracking-tighter text-[#ff2a85] drop-shadow-[2px_2px_0px_#000]"')
html = html.replace('class="stat-num"', 'class="font-display font-black text-5xl tracking-tighter drop-shadow-[2px_2px_0px_#000]"')
html = html.replace('class="stat-label"', 'class="font-body font-bold text-sm uppercase mt-2"')
html = html.replace('class="field-label"', 'class="block font-display text-xs font-black uppercase tracking-wider text-black mb-2"')
html = html.replace('class="chain-row"', 'class="flex items-center gap-4 py-3 border-b-[3px] border-black last:border-0"')

# Toggles
html = html.replace('class="toggle-wrap"', 'class="flex items-center gap-3 cursor-pointer"')
html = html.replace('class="toggle"', 'class="relative inline-block w-12 h-6 border-[3px] border-black bg-white"')
html = html.replace('class="toggle-track"', 'class="absolute top-0.5 left-0.5 w-3.5 h-3.5 bg-black transition-transform"')
# Need to make toggle work with Alpine visually. A real neobrutal toggle moves the inner square.
# Let's rely on Alpine's x-model:
html = html.replace('class="toggle-track"', 'class="absolute top-0.5 left-0.5 w-3.5 h-3.5 bg-black transition-transform duration-200" :class="{ \'translate-x-6 bg-[#00e676]\': $el.previousElementSibling.checked }"')
html = html.replace('<input type="checkbox" ', '<input type="checkbox" class="sr-only" ')
html = html.replace('class="toggle-label"', 'class="font-display font-bold text-sm uppercase"')


# Save back
with open(html_path, "w", encoding="utf-8") as f:
    f.write(html)

print("Rewrote index.html with Tailwind utility classes.")
