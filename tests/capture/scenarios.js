// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Synthetic Lt view models covering every reader in capture.js, for the tests that need a
 * whole module: schema and rail validation of capture.js output (test_capture_validates.py)
 * and the capture -> sink -> stamp chain (test_end_to_end.py). All text is invented.
 */
'use strict';
const H = require('./vm_harness.js');
const { obs, coll, mdl, contentPage, reportPage, checkpointPage, panel, baseRoot } = H;

const trow = (...t) => mdl({ cells: coll(...t.map(x => mdl({ content: x }))) });
const hrow = (...t) => mdl({ headerRow: true, cells: coll(...t.map(x => mdl({ content: x }))) });

/* Every panel shape: authored text, image, author table, divider, hotspot, unknown content,
 * text, multiple choice, table with an author grid, both annotation modes, both labelling
 * keys, categories, a subpage with a child, a notebook, an unmapped widget; then a checkpoint
 * page, a page of multiple-choice items (RAT candidate), a mirror page and a report page. */
function demoEnv(trips = []) {
  const intro = contentPage('Introduction', [
    panel('kcTextArea', '11', { theText: obs('<p>Read the case.</p><p>Then answer.</p>') }),
    panel('image', '12', { imageResourceKey: '70', caption: obs('Figure 1'), altTextDescription: obs('A diagram') }),
    panel('table', '13', { rows: coll(hrow('Term', 'Meaning'), trow('Alpha', 'First letter')) }),
    panel('divider', '14', {}),
    panel('hotspot', '15', { hotspots: coll(
      mdl({ id: 1, parentHotspot: null, hotspotType: 'CUSTOM', imageResourceKey: '71', labelTitle: '', labelDescription: '' }),
      mdl({ id: 2, parentHotspot: 1, hotspotType: 'POINT', hotspotColour: 'RED', labelTitle: 'Region A',
            labelDescription: 'The first region', position: '{"points":[{"x":0.1,"y":0.2}]}' })) }),
    panel('kcMystery', '16', { widgetConfig: 1 }),
    panel('kcTextArea', '17', { theText: obs('<p>Copyright © Example Press</p>') })
  ]);
  const work = contentPage('Questions', [
    panel('text-question', '21', { question: obs('<p>Explain the finding.</p>'), assessmentType: obs('noAssessment') },
          { answer: obs('An invented answer of some length.') }),
    panel('select', '22', { question: obs('Pick the best option'), answers: coll(mdl({ answer: 'Option one' }), mdl({ answer: 'Option two' })),
                            assessmentType: obs('instantFeedback') }, { selectedAnswers: obs([1]) }),
    panel('table-question', '23', { question: obs('Complete the grid'),
                                    rows: coll(hrow('Item', 'Value'), trow('Row A', ''), trow('Row B', '')) },
          { rows: coll(hrow('Item', 'Value'), trow('Row A', 'filled'), trow('Row B', '')) }),
    panel('image-annotation', '24', { inputMode: obs('allowImageAndAnnotation'), questionText: obs('Upload and mark') },
          { annotations: obs([]), imageKey: obs(''), presentedRatio: obs(0) }),
    panel('image-annotation', '25', { inputMode: obs('allowAnnotation'), questionText: obs('Mark the image'),
                                      predefinedImageKey: obs('72'), presentedRatio: obs(0.75) },
          { annotations: obs([mdl({ type: 'text', text: 'here', position: mdl({ x: 0.5, y: 0.5 }) })]) }),
    panel('image-annotation', '26', { inputMode: obs('allowImageAndAnnotation'), questionText: obs('Upload a sketch'),
                                      predefinedImageKey: obs('73') },
          { annotations: obs([]), imageKey: obs('74'), presentedRatio: obs(1.25) }),
    panel('kcImageLabelling', '27', { question: obs('Label it'), imageResourceKey: '75', presentedRatio: obs(1.1),
                                      labelItems: coll(mdl({ label: 'beta' }), mdl({ label: 'alpha' })),
                                      labelLocationItems: coll(mdl({}), mdl({})) }, { linkedLabels: coll() }),
    panel('drag-and-drop', '28', { question: obs('Drag them'), imageResourceKey: '76', presentedRatio: obs(1.0),
                                   singleAttempt: obs(true),
                                   labelItems: coll(mdl({ label: 'gamma' })), labelLocationItems: coll(mdl({})) },
          { linkedLabels: coll(mdl({ labelIndex: 0, locationIndex: 0 })) }),
    panel('categories', '29', { question: obs('Sort them'), categoryItems: coll(mdl({ title: 'Bin one' }), mdl({ title: 'Bin two' })),
                                labelItems: coll(mdl({ label: 'thing a' }), mdl({ label: 'thing b' })) },
          { categoryAnswerItems: coll(mdl({ labelAnswerItems: coll() }), mdl({ labelAnswerItems: coll() })) }),
    panel('subpage', '30', { subpages: coll(mdl({ rows: coll(mdl({ cells: coll(mdl({ panels: coll(
      mdl({ pluginKey: 'text-question', panelKey: '31', panelState: { content: { question: 'Inner question' }, input: { answer: '' } } })
    ) })) })) })) }),
    panel('notebook', '32', { question: obs('Notes') }, { notes: obs('') }),
    panel('kcNewWidget', '33', { question: obs('New kind') }, { value: obs(1) })
  ]);
  const check = contentPage('Check', [
    panel('select', '41', { question: obs('Check one'), answers: coll(mdl({ answer: 'A' }), mdl({ answer: 'B' })) }, { selectedAnswers: obs([]) }),
    panel('select', '42', { question: obs('Check two'), answers: coll(mdl({ answer: 'A' }), mdl({ answer: 'B' })) }, { selectedAnswers: obs([]) })
  ]);
  const later = contentPage('Summary', [
    panel('text-question', '51', { question: obs('<p>Explain the finding.</p>') }, { answer: obs('An invented answer of some length.') })
  ]);
  const pages = [intro, work, checkpointPage(9, false), check, later, reportPage()];
  return { root: baseRoot(pages, [], trips) };
}

module.exports = { demoEnv };
