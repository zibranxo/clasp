# EXACT KeyKing UI Replica Implementation Plan

## Goal
Rebuild CLASP UI (`clasp/ui/static/`) to be an **exact pixel-perfect replica** of KeyKing interface (`web/desktop/`)

## Analysis: Key Differences

### Current CLASP UI
- Alpine.js based (no build step)
- Single-page app with panels
- Tailwind + custom CSS
- No React/TypeScript

### KeyKing UI (Target)
- React + TypeScript + Vite
- Multi-page routing (React Router)
- Tauri desktop app
- Complex state management
- Tour system
- Authentication flow

## Implementation Strategy

### Phase 1: Structural Rebuild (HTML/CSS)
**Goal**: Match KeyKing layout exactly using Alpine.js

#### Key Components to Replicate:
1. **Sidebar Navigation** (exact width, styling, icons)
2. **Main Layout** (padding, spacing, max-width)
3. **Card System** (borders, shadows, hover effects)
4. **Button System** (all variants with exact styling)
5. **Typography** (exact fonts, sizes, weights)
6. **Color Palette** (exact Neo colors)
7. **Animations** (slide-in, fade-in, etc.)

### Phase 2: Functional Rebuild (JavaScript)
**Goal**: Replicate KeyKing functionality with Alpine.js

#### Key Features to Implement:
1. **Provider Keys Panel** (exact layout)
2. **Dashboard Panel** (KPI cards, provider cards)
3. **Routing Logs Panel** (table, filtering)
4. **Settings Panel** (all settings)
5. **Status Indicators** (exact positioning)
6. **Tour System** (if possible with Alpine)

### Phase 3: Visual Polish
**Goal**: Pixel-perfect matching

#### Key Details:
1. **Icons**: Use same Lucide icons
2. **Spacing**: Exact padding/margins
3. **Borders**: 3px black everywhere
4. **Shadows**: Hard shadows (2px, 4px, 8px)
5. **Hover Effects**: Exact transforms

## Detailed Component Mapping

### Sidebar Navigation
**KeyKing**:
```html
<aside class="w-[280px] bg-neo-bg border-r-3 border-neo-dark...">
  <div class="p-6 flex items-center gap-3 border-b-3 border-neo-dark bg-white">
    <div class="w-10 h-10 bg-neo-yellow...">◈</div>
    <h1 class="text-2xl font-display font-black...">KeyKing</h1>
  </div>
  <nav class="flex-1 px-4 py-6 space-y-3 font-display uppercase font-bold text-sm">
    <!-- Nav links with icons -->
  </nav>
  <div class="p-5 m-4 bg-neo-green border-3 border-neo-dark...">
    <!-- Status indicator -->
  </div>
</aside>
```

**CLASP Implementation**:
```html
<aside class="neo-sidebar">
  <div class="sidebar-header">
    <div class="logo">◆ CLASP</div>
    <h1 class="font-display font-black text-2xl">CLASP</h1>
  </div>
  <nav class="sidebar-nav">
    <!-- Replicate exact nav structure -->
  </nav>
  <div class="sidebar-footer">
    <!-- Status with exact styling -->
  </div>
</aside>
```

### Provider Cards
**KeyKing**:
```html
<div class="border-[3px] border-black bg-white shadow-[4px_4px_0px_0px_#000] p-6 mb-6...">
  <div class="flex items-center justify-between cursor-pointer mb-2 gap-4 border-b-[3px] border-black pb-4 mb-4">
    <div class="flex-1">
      <span class="font-display font-black uppercase text-2xl">NVIDIA NIM</span>
      <span class="neo-badge">HEALTHY</span>
    </div>
    <label class="enable-toggle">...</label>
    <svg class="chevron">...</svg>
  </div>
  <div x-show="open" x-collapse>
    <!-- Card body -->
  </div>
</div>
```

**CLASP Implementation**: Exact replica with Alpine.js bindings

## Timeline

### Phase 1: Structural Rebuild (4-6 hours)
- Rebuild HTML structure to match KeyKing exactly
- Implement CSS to replicate every visual detail
- Set up base Alpine.js structure

### Phase 2: Functional Rebuild (6-8 hours)
- Implement provider keys panel
- Build dashboard with KPI cards
- Create routing logs table
- Build settings panel
- Implement status system

### Phase 3: Visual Polish (2-4 hours)
- Pixel-perfect alignment
- Exact icon matching
- Animation tuning
- Responsive adjustments

**Total**: 12-18 hours

## Success Criteria

✅ **Exact visual match** (pixel-perfect)
✅ **Same component structure**
✅ **Identical color palette**
✅ **Matching typography**
✅ **Consistent spacing/borders**
✅ **All functionality preserved**
✅ **Responsive design maintained**

## Next Steps

1. **Start with HTML structure** - Rebuild from scratch
2. **Implement CSS** - Exact styling replication
3. **Add Alpine.js** - Maintain functionality
4. **Test thoroughly** - Ensure no regressions

Let me begin implementation now.