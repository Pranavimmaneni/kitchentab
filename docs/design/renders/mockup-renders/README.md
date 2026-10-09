# Mockup renders (for VIS-01 and visual review)

`png/` holds every approved mockup rendered at 390 px wide, 2x scale, in light and dark
(`<Screen>_light.png`, `<Screen>_dark.png`). Long screens are rendered at their full height.
Use them as the visual reference when building screens and when approving VIS-01 baselines.
They are references, not pixel-exact baselines: the real app's baselines are screenshots of
the build that Pranav approves.

`tool/render.py` re-renders the `.dc.html` files itself (a minimal template expander that
fills `{{holes}}`, `<sc-for>` and `<sc-if>` from each file's `renderVals()`; event handlers
are ignored, so it shows each screen's default state; pass state as JSON to render others).

    cd docs/design/renders/tool && npm install && pip install playwright
    cd ../.. && python3 renders/tool/render.py renders/png light 1 Kitchen.dc.html 'Fridge.dc.html:{"sel":"chicken"}'

Arguments: output dir, `light|dark`, `1` = full height, then files (optionally `:stateJSON`).
