# Image Placeholder Skill

## Purpose

This Skill gives the Portfolio Agent guidelines for suggesting, at each case study
section, what image the designer should add there.

No image is ever retrieved, attached, or analyzed by this agent - every suggestion is a
placeholder for the user to fill in themselves, derived only from the text already
written in that section. This Skill applies to every case study, not just ones where
images happen to be available, since it never depends on actual image content.

## Guidance

For each section included in the case study draft, after writing that section's content,
add one placeholder suggestion describing a plausible, specific image tied to something
concretely stated in that section - for example "a screenshot of the settings screen
described above" or "a photo of the early paper prototypes mentioned in this section."

Avoid generic filler such as "an image related to this section" or "a relevant visual" -
the suggestion should be specific enough that the designer immediately knows what to go
find or create.

Write the suggestion so it also works as a caption in its own right: a reader who only
scans section titles and these suggestions - without reading the surrounding prose -
should still come away understanding what the project was about.

Skip a section only if nothing concrete in its text would support a specific suggestion
(for example, a very short or purely reflective section). Default to including one
placeholder per section rather than skipping.

## Authenticity Check

- Never phrase a suggestion as if a real image was found, reviewed, or attached - it is a
  suggestion for what to add, not a description of something that exists.
- Never invent specifics (a color, a number, a name, a screen name) beyond what the
  section's own text already states.
- If a section's text is too vague to ground a specific suggestion, say so is preferable
  to inventing detail that isn't there.

## Output Format

Place each suggestion directly after the section it belongs to, as plain text (no
markdown, matching the agent's overall output constraint), using a clearly
distinguishable bracketed marker so it doesn't read as ordinary prose:

```
[Suggested image: a screenshot of the completed onboarding flow described above.]
```

One suggestion per section, immediately following that section's content and before the
next section title.
