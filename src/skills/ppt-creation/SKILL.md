---
name: ppt-creation
description: Create presentations and slides
slash: true
---

# Presentation Creation Skill

This skill helps you create presentations and slides.

## Usage

Use this skill when the user wants to:
- Create a presentation from scratch
- Convert a report to slides
- Generate slide content
- Format presentation materials

## Workflow

1. **Understand the topic** and audience
2. **Outline the structure** - title, sections, key points
3. **Generate content** for each slide
4. **Format as Markdown** or generate PPTX if python-pptx is available

## Output Format

Presentations are generated as structured Markdown:

```
# Presentation Title

## Slide 1: Title Slide
- Subtitle
- Author/Date

## Slide 2: Overview
- Key point 1
- Key point 2
- Key point 3

## Slide 3: Details
...
```

## Tips

- Keep slides concise - max 6 bullets per slide
- Use clear headings
- Include speaker notes when helpful
- Suggest visual elements (charts, images)
