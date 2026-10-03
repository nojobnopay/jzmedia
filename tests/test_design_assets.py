"""Portable asset contracts. These checks do not need a renderer or a running app."""
import importlib.util
import hashlib
import shutil
import struct
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_design', ROOT / 'scripts/build_design.py')
design = importlib.util.module_from_spec(spec)
spec.loader.exec_module(design)


def test_navigation_forward_is_distinct_from_playback_forward():
    manifest = design.read_json('assets.json')
    assert design.canonical('forward', manifest) == 'arrow-right'
    assert design.canonical('forward', manifest, 'player') == 'skip-forward-10'
    assert design.canonical('forward10', manifest, 'tv') == 'skip-forward-10'
    assert design.canonical('rewind', manifest, 'player') == 'skip-back-10'
    assert design.canonical('movies', manifest, 'tv') == 'movie'


def test_all_functional_masters_are_portable_and_have_provenance():
    manifest = design.read_json('assets.json')
    for asset in manifest['assets']:
        assert asset['source'] and asset['history'] and asset['revision'] >= 1
        if asset['category'] != 'brand':
            assert design.icon_paths(asset)
        root = ET.parse(ROOT / asset['master']).getroot()
        assert all(element.tag.endswith('svg') or element.tag.endswith('path') for element in root.iter())
        assert all('transform' not in element.attrib for element in root.iter())


def test_brand_ico_has_three_real_optical_size_layers():
    for folder in ('frontend/public', 'docs/assets/design'):
        data = (ROOT / folder / 'favicon.ico').read_bytes()
        assert struct.unpack('<HHH', data[:6]) == (0, 1, 3)
        actual = []
        for index in range(3):
            width, height, _, _, _, _, size, offset = struct.unpack('<BBBBHHII', data[6+16*index:22+16*index])
            payload = data[offset:offset+size]
            assert payload.startswith(b'\x89PNG\r\n\x1a\n')
            assert struct.unpack('>II', payload[16:24]) == (width, height)
            actual.append(width)
        assert actual == [16, 32, 48]


def test_every_source_reference_and_native_exception_is_registered():
    manifest = design.read_json('assets.json')
    registry = {a['id']: design.icon_paths(a) for a in manifest['assets'] if a['category'] != 'brand'}
    requirements, errors = design.inventory(manifest, registry, design.read_json('contracts.json'))
    assert not errors, '\n'.join(errors)
    assert len(requirements) > 500
    assert {'web', 'mobile-web', 'android-tv'} == {p for r in requirements for p in r['platforms']}
    assert any(r['dynamic_expression'] for r in requirements)
    assert any(r['classification'] == 'native control' for r in requirements)


def test_declared_export_pixel_sizes_match_output_files():
    assert design.validate_rasters(design.brand_outputs()) == []


def test_new_function_glyph_is_rejected_but_documented_text_is_allowed(tmp_path, monkeypatch):
    component = tmp_path / 'frontend/src/Demo.vue'
    component.parent.mkdir(parents=True)
    component.write_text('<template><button>⚙</button><p>路径 A → 路径 B</p></template>\n')
    monkeypatch.setattr(design, 'ROOT', tmp_path)
    errors = design.glyph_errors({'text_symbols': [{
        'source': 'frontend/src/Demo.vue', 'symbols': '→', 'contexts': ['路径 A → 路径 B'],
    }]})
    assert len(errors) == 1 and "'⚙'" in errors[0]
    component.write_text('<!-- ⚙ -->\n<script>// ▶\nconst hint = "设置"</script>')
    assert design.glyph_errors({}) == []


def test_unknown_dynamic_ternary_output_is_rejected(tmp_path, monkeypatch):
    manifest = design.read_json('assets.json')
    registry = {a['id']: design.icon_paths(a) for a in manifest['assets'] if a['category'] != 'brand'}
    component = tmp_path / 'frontend/src/Demo.vue'
    component.parent.mkdir(parents=True)
    component.write_text('''<template><AppIcon :name="mode === 'condition-value' ? 'settings' : 'unknown-icon'" /></template>''')
    monkeypatch.setattr(design, 'ROOT', tmp_path)
    _, errors = design.inventory(manifest, registry, {'dynamic_icons': [], 'specialized_controls': []})
    assert any('unknown icon unknown-icon' in error for error in errors)
    assert not any('condition-value' in error for error in errors)


def test_generic_adapters_do_not_claim_every_asset_is_actually_used():
    rows = design.read_json('requirements.json')['requirements']
    adapters = [row for row in rows if row['category'] == 'adapter']
    assert adapters
    assert all(not row['assets'] and row['supported_assets'] for row in adapters)
    keyboard = next(row for row in rows if row['element'] == 'Button' and row['display_size']['profile'] == 'tv-keyboard')
    assert 'keyHeight' in keyboard['display_size']['value']


def test_android_colors_resolve_the_same_semantic_tokens_as_web():
    tokens = design.read_json('tokens.json')
    assert all(set(reference) == {'token'} for reference in tokens['android']['colors'].values())
    tokens['web']['--jz-accent'] = '#abcdef'
    css, kotlin = design.token_outputs(tokens)
    assert '--jz-accent: #abcdef;' in css
    assert 'val Accent = Color(0xFFABCDEF)' in kotlin


def test_export_revisions_include_each_optical_master():
    manifest = design.read_json('assets.json')
    for asset in manifest['assets']:
        if asset['id'] == 'brand-mark': asset['revision'] = 7
        if asset['id'] == 'brand-favicon-16': asset['revision'] = 3
    exports = design.brand_outputs(manifest)
    ico = next(item for item in exports if item['path'] == 'frontend/public/favicon.ico')
    assert ico['master_revision'] == 7
    assert ico['source_revisions'] == {'brand-favicon-16': 3, 'brand-favicon-32': 1, 'brand-mark': 7}
    assert ico['source_assets'] == ['brand-favicon-16', 'brand-favicon-32', 'brand-mark']


@pytest.mark.parametrize('symbol', ['🏠', '&#9881;', '&#x2699;', r'\u2699', r'\u{1F3E0}', r'\U0001F3E0', r'\1F3E0 '])
def test_encoded_symbols_and_unlisted_emoji_cannot_bypass_review(tmp_path, monkeypatch, symbol):
    component = tmp_path / 'frontend/src/Demo.vue'
    component.parent.mkdir(parents=True)
    component.write_text(f'<template><p>{symbol}</p><p>中文说明 / 1920×1080 / −0.5s</p></template>')
    monkeypatch.setattr(design, 'ROOT', tmp_path)
    errors = design.glyph_errors({})
    assert len(errors) == 1


def test_comment_markers_inside_ui_text_do_not_hide_glyphs(tmp_path, monkeypatch):
    component = tmp_path / 'frontend/src/Demo.vue'
    component.parent.mkdir(parents=True)
    component.write_text('''<template><p>// 🏠</p><p title="<!-- ⚙ -->">说明</p></template>
<script setup>
const hint = "说明 // ▶"
// ✓ 真正的代码注释
</script>
<!-- ★ 真正的HTML注释 -->''')
    monkeypatch.setattr(design, 'ROOT', tmp_path)
    errors = design.glyph_errors({})
    assert len(errors) == 3
    assert all(not any(symbol in error for symbol in ('✓', '★')) for error in errors)


@pytest.mark.parametrize('markup', [
    '<app-icon name="unknown-icon" />',
    '<app-icon v-bind:name="mode ? \'search\' : \'unknown-icon\'" />',
    '<jz-button v-bind:icon="mode ? \'search\' : \'unknown-icon\'">操作</jz-button>',
    '<player-icon name="unknown&#45;icon" />',
    '<AppIcon :name="mode ? \'search\' : dynamicName" />',
    '<AppIcon v-bind="{ name: \'unknown-icon\' }" />',
    '<JzButton v-bind="{ icon: \'unknown-icon\' }">操作</JzButton>',
    '<AppIcon name="brand-unknown" />',
])
def test_alternate_vue_bindings_are_checked_after_script_first(tmp_path, monkeypatch, markup):
    manifest = design.read_json('assets.json')
    registry = {a['id']: design.icon_paths(a) for a in manifest['assets'] if a['category'] != 'brand'}
    component = tmp_path / 'frontend/src/Demo.vue'
    component.parent.mkdir(parents=True)
    component.write_text('<script setup>const mode = true</script>\n<template>' + markup + '</template>')
    monkeypatch.setattr(design, 'ROOT', tmp_path)
    _, errors = design.inventory(manifest, registry, {'dynamic_icons': [], 'specialized_controls': []})
    assert errors


def test_conditions_do_not_count_as_assets_and_partial_dynamic_outputs_fail():
    assert design.expression_values("state === 'condition' ? 'play' : value ? 'pause' : 'close'") == ['play', 'pause', 'close']
    assert design.expression_values("(state?.ready ?? false) ? 'play' : 'pause'") == ['play', 'pause']
    assert design.expression_values("ready ? 'play' : dynamicName") is None
    assert design.kotlin_expression_values('if (ready) "play" else if (paused) "pause" else "close"') == ['play', 'pause', 'close']
    assert design.kotlin_expression_values('if (ready) "play" else dynamicName') is None
    assert design.icon_property_values('const items = [{icon: "play"}, {"icon": \'pause\'}]') == (['play', 'pause'], [])
    assert design.icon_property_values('const items = [{icon: dynamicName}]')[1]


def test_monochrome_master_cannot_diverge_between_svg_and_compose(tmp_path, monkeypatch):
    source = tmp_path / 'bad.svg'
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3h18v18H3Z" fill="#f00" /></svg>')
    monkeypatch.setattr(design, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='none or currentColor'):
        design.icon_paths({'master': 'bad.svg', 'grid': [24, 24]})


def test_check_rejects_unrendered_masters_without_writing_any_file(tmp_path, monkeypatch, capsys):
    # A temporary repository snapshot avoids touching the working tree or product data.
    for directory in ('design', 'frontend/src', 'frontend/public', 'android-tv/app/src/main/java',
                      'android-tv/app/src/main/res', 'docs/assets/design'):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    monkeypatch.setattr(design, 'ROOT', tmp_path)
    monkeypatch.setattr(design, 'DESIGN', tmp_path / 'design')
    monkeypatch.setattr(design.sys, 'argv', ['build_design.py', '--check'])
    master = tmp_path / 'design/brand/mark.svg'
    original = master.read_text()
    master.write_text(original + '<!-- unrendered revision -->\n')
    def snapshot():
        return {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in tmp_path.rglob('*') if p.is_file()}
    before = snapshot()
    assert design.main() == 1
    assert snapshot() == before, '--check must not repair or rewrite stale files'
    assert 'design/brand/mark.svg: stale or modified raster source/output' in capsys.readouterr().err
    master.write_text(original)
    functional = tmp_path / 'design/icons/close.svg'
    functional.write_text(functional.read_text().replace('m6 6', 'm7 6'))
    before = snapshot()
    assert design.main() == 1
    assert snapshot() == before
    errors = capsys.readouterr().err
    assert 'frontend/src/generated/design-icons.js: generated output is stale' in errors
    assert 'android-tv/app/src/main/java/org/jzmedia/tv/ui/generated/DesignIcons.kt: generated output is stale' in errors
