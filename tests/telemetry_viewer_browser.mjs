import { buildViewer } from '../viewer/build.mjs';
import { syntheticDemo } from '../viewer/demo.mjs';

export async function checkTelemetryViewer({ client, setViewport, runInPage, assert }) {
  const { data, timing } = syntheticDemo();
  // Do not inherit the public terminal's hash or persisted display preferences.
  const { frameId } = await client.send('Page.navigate', { url: 'about:blank' });
  for (const [width, height, elapsed] of [[1440, 804, timing], [390, 712, null]]) {
    await setViewport(width, height);
    await client.send('Page.setDocumentContent', { frameId, html: buildViewer(data, elapsed) });
    const initial = await runInPage(`
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const find = key => document.querySelector('[data-role="' + key + '"]');
      const roleNames = ['speed', 'brake', 'throttle', 'path'${elapsed ? ", 'delta'" : ''}];
      const box = find('brake').getBoundingClientRect();
      const consoleBox = document.querySelector('.terminal-console').getBoundingClientRect();
      const frame = document.querySelector('.terminal-frame').getBoundingClientRect();
      return {
        boot: !!window.orcPreview,
        view: window.orcPreview?.state().view,
        track: find('track').textContent,
        lapOptions: find('lap').options.length,
        lapDeltas: [...find('laps').rows].map(row => row.cells[2].textContent),
        deltaHeight: find('delta').getBoundingClientRect().height,
        fits: consoleBox.bottom <= innerHeight && consoleBox.right <= innerWidth,
        borderless: frame.x === 0 && frame.y === 0 && frame.width === innerWidth && frame.height === innerHeight,
        canvases: roleNames.map(role => {
          const c = find(role);
          const pixels = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
          return pixels.filter((v, i) => i % 4 === 3 && v > 0).length;
        }),
        box: { x: box.x, y: box.y, width: box.width, height: box.height },
      };
    `);
    assert(initial.boot && initial.view === 'main' && initial.track === data.track, 'Synthetic viewer must load the input circuit in Main', initial);
    assert(initial.lapOptions === 2 && initial.lapDeltas.join(',') === '0.000,+1.250,+2.625,--', 'Lap rows and exclusions must match the input', initial);
    assert((initial.deltaHeight > 0) === Boolean(elapsed), 'Only supplied timing reserves a delta row', initial);
    assert(initial.fits && initial.borderless && initial.canvases.every(n => n > 100), 'Visible CRT layout and plots must fit', initial);
    const { x, y, width: w, height: h } = initial.box;
    const mid = y + h / 2;
    for (const [type, p] of [['mousePressed', .75], ['mouseMoved', .25], ['mouseReleased', .25]]) {
      await client.send('Input.dispatchMouseEvent', {
        type, x: x + 64 + p * (w - 74), y: mid, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1,
      });
    }
    const interaction = await runInPage(`
      const viewer = window.orcPreview;
      const zoom = viewer.state().range;
      document.querySelector('[aria-label="Compare lap 3"]').click();
      const switched = viewer.state();
      const bezel = document.getElementById('bezelToggle');
      bezel.click();
      const framed = bezel.getAttribute('aria-pressed');
      bezel.click();
      document.querySelector('[data-role="zoom-reset"]').click();
      const reset = viewer.state().range;
      viewer.runCommand('coach');
      const report = document.getElementById('terminalPageText').textContent;
      return { zoom, switched, framed, reset, report };
    `);
    assert(interaction.zoom.join(',') === '0.25,0.75' && interaction.switched.range.join(',') === '0.25,0.75' && interaction.switched.lap === 3,
      'Native drag zoom must survive a Comparison Lap switch', interaction);
    assert(interaction.framed === 'true' && interaction.reset.join(',') === '0,1', 'Bezel and reset controls must work', interaction);
    assert(interaction.report.includes('REFERENCE LAP 1 / 59.000 s'), 'Report must show the provided Reference Lap', interaction);
  }
}
