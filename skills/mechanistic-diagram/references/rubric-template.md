<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Rubric template

A generic grading rubric for a mechanism diagram. Replace it with the rubric your course
uses: set `diagram.rubric_path` in the lt-suite config to that file, and the grading steps
read it in place of this one. Keep the course's own wording in your copy; do not paste it
into issues or shared places.

Four levels, lowest first: **Missing**, **Partial**, **Meets**, **Exceeds**. Grade each
criterion on its own. A diagram is ready to hand over when every criterion reaches
**Meets**; **Exceeds** is the aim.

## 1. Accuracy of steps and connections

| Level | What the diagram shows |
|---|---|
| Missing | Several steps or connectors are wrong, reversed or unsupported by the case. |
| Partial | Most connectors are right, but some links between steps are vague, skipped or point the wrong way. |
| Meets | Every step is correct, every connector has the right kind and direction, and each finding is explained by a chain. |
| Exceeds | As Meets, and the chains join the underlying science (anatomy, physiology, biochemistry, pathology, pharmacology) to the clinical picture with no gap a reader has to fill. |

## 2. Coverage of the case

| Level | What the diagram shows |
|---|---|
| Missing | Many findings, results or key ideas of the case are absent. |
| Partial | Most key ideas are present; one or two findings or steps are missing. |
| Meets | Every key idea, finding and result of the case has a place in the diagram. |
| Exceeds | As Meets, plus supporting ideas that show depth: why a test was chosen, what a ruled-out diagnosis would have looked like, how each treatment acts on the mechanism. |

## 3. Organisation and clarity

| Level | What the diagram shows |
|---|---|
| Missing | The reading order is unclear; connectors cross, crowd or cannot be followed. |
| Partial | Parts read well, but some stages are cluttered or out of order. |
| Meets | The whole diagram reads in story order, stage by stage, and every connector can be followed from start to end. |
| Exceeds | As Meets, and the layout itself teaches: the key steps stand out, the time course is visible, and the page reads at a glance. |

## How the skill uses it

- Before writing content, read the rubric and list what each criterion needs for this case.
- After the build, grade the diagram criterion by criterion (`references/grade-through.md`).
- For each criterion, state the level and one clause on what would lift it to the next level.
- Fix everything below **Meets** before handing the diagram over.
