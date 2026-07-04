# CLASP UI Redesign Implementation Plan

## Goal
Apply the same design system from `web/desktop` (KeyKing) to the CLASP UI in `clasp/ui/static/`

## Design System Analysis

### Color Palette (Neo-Brutalism)
```css
neo-bg: #fcf6e6       /* Warm off-white background */
neo-yellow: #fde047  /* Primary accent */
neo-green: #00e676    /* Success states */
neo-pink: #ff2a85     /* Errors, CTAs */
neo-cyan: #00f0ff     /* Info, highlights */
neo-purple: #9d4edd   /* Secondary accent */
neo-orange: #ff6d00   /* Warnings */
neo-dark: #000000     /* Borders, text */
neo-light: #ffffff     /* White */
```

### Typography
- **Display**: Space Grotesk (black, bold, uppercase)
- **Body**: Lexend (regular, medium)
- **Mono**: JetBrains Mono (for code, keys, stats)

### Key Design Elements
1. **Borders**: 3px solid black borders everywhere
2. **Shadows**: Hard shadows (2px, 4px, 8px, 12px) with no blur
3. **Hover Effects**: Translate -1px on hover for depth
4. **Icons**: Lucide React icons throughout
5. **Layout**: Fixed sidebar (280px), fluid main content

## Implementation Plan

### Phase 1: CSS Overhaul
**File**: `clasp/ui/static/style.css`

**Tasks**:
1. Replace Tailwind colors with Neo palette
2. Add Neo-Brutalism border and shadow utilities
3. Update font family definitions
4. Add custom scrollbar styling
5. Create component classes (cards, buttons, badges)

### Phase 2: HTML Structure Update
**File**: `clasp/ui/static/index.html`

**Tasks**:
1. Update HTML structure to match KeyKing layout
2. Add proper semantic HTML5 elements
3. Implement fixed sidebar navigation
4. Add main content area with proper spacing
5. Include Lucide icon SVG definitions

### Phase 3: JavaScript Refactoring
**File**: `clasp/ui/static/app.js`

**Tasks**:
1. Update navigation structure to match KeyKing
2. Add proper page routing (currently uses Alpine x-show)
3. Implement sidebar state management
4. Add animation controllers for transitions
5. Update event handlers for new UI elements

### Phase 4: Component-by-Component Redesign

#### Sidebar Navigation
- Fixed width (280px)
- Logo with icon
- Navigation links with active states
- Status indicator at bottom

#### Header
- Remove current header
- Move status to sidebar
- Simplify to just page title area

#### Provider Cards
- Add 3px black borders
- Implement Neo shadow system
- Update typography to Space Grotesk
- Add hover translate effects
- Implement proper status badges

#### Buttons
- Add 3px borders
- Implement Neo color scheme
- Add hover translate effects
- Update to uppercase labels

#### Input Fields
- Add 3px borders
- Implement proper focus states
- Add mono font for keys
- Include reveal toggle icons

#### Tables
- Add proper borders
- Implement zebra striping
- Update header styling
- Add action buttons

### Phase 5: Responsive Design

**Tasks**:
1. Implement mobile-friendly sidebar (collapsible)
2. Add responsive breakpoints
3. Ensure touch-friendly button sizes
4. Test on multiple screen sizes

### Phase 6: Browser Testing

**Tasks**:
1. Test in Chrome, Firefox, Safari, Edge
2. Verify all animations work
3. Check responsive behavior
4. Validate form submissions
5. Test SSE connections

## Detailed Component Mapping

### Current CLASP UI → KeyKing Design

#### Navigation
- **Current**: Emoji icons, simple list
- **New**: Lucide icons, proper active states, 3px borders

#### Provider Cards
- **Current**: Tailwind gray cards
- **New**: White cards with 3px black borders, Neo shadows

#### Buttons
- **Current**: Simple Tailwind buttons
- **New**: 3px borders, uppercase, hover translate, Neo colors

#### Status Badges
- **Current**: Simple pills
- **New**: 3px borders, proper color coding, shadows

#### Typography
- **Current**: System fonts
- **New**: Space Grotesk (headings), Lexend (body), JetBrains Mono (code)

## Timeline Estimate

- **Phase 1 (CSS)**: 2-3 hours
- **Phase 2 (HTML)**: 3-4 hours  
- **Phase 3 (JS)**: 4-5 hours
- **Phase 4 (Components)**: 6-8 hours
- **Phase 5 (Responsive)**: 2-3 hours
- **Phase 6 (Testing)**: 3-4 hours

**Total**: 20-27 hours

## Risk Assessment

### High Risk
- Alpine.js compatibility with new structure
- SSE connection stability after changes
- Form submission handling

### Medium Risk
- Responsive design implementation
- Browser compatibility
- Animation performance

### Low Risk
- Color palette changes
- Typography updates
- Icon replacements

## Success Criteria

✅ All UI elements match KeyKing design system
✅ No functional regressions
✅ All buttons and forms work
✅ SSE streams remain stable
✅ Responsive on all screen sizes
✅ Cross-browser compatible
✅ Animation performance ≥ 60fps

## Next Steps

1. Start with CSS overhaul (Phase 1)
2. Update HTML structure (Phase 2)
3. Refactor JavaScript (Phase 3)
4. Redesign components (Phase 4)
5. Implement responsive design (Phase 5)
6. Comprehensive testing (Phase 6)
