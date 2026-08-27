# UI Design Direction

OpenRAW Studio should be easy to use, but still feel premium.

The target feeling:

```text
simple to start
calm to look at
polished in motion
powerful when expanded
```

In Chinese:

```text
上手必须简单，界面要高级、干净、安静，但不能难用。
```

## Product Personality

OpenRAW Studio is not a noisy tool full of sliders on the first screen. It should
feel closer to a careful photo workspace:

- minimal
- precise
- calm
- modern
- trustworthy
- visually refined
- beginner-friendly
- powerful underneath

Apple-style design can be a reference point: clean layout, strong spacing,
careful typography, soft motion, direct manipulation, and clear hierarchy.

Do not copy Apple branding or visual assets. Use the design principles, not the
brand.

## First Screen

The first screen should answer one question:

```text
What photo do you want to process?
```

Primary actions:

- import RAW photo
- choose output folder
- run AUTO
- preview result
- export

The user should not need to understand RAW engines, masks, models, recipes, LUTs,
or color science before getting a result.

## Beginner Flow

The beginner flow should be almost this simple:

```text
Open app
  -> import RAW
  -> click AUTO
  -> compare before/after
  -> export
```

Beginner mode should expose:

- AUTO
- before/after
- creative look
- export

Everything else should be discoverable, not forced.

## Advanced Flow

Advanced controls should expand from the same workspace:

- RAW
- Portrait
- Color
- Film
- Export
- Debug/QC

These controls should feel like professional tools, but grouped clearly. Avoid
showing every future feature at once.

## Visual Style

Preferred direction:

- clean neutral background
- strong photo-first layout
- careful spacing
- subtle shadows only where useful
- restrained color
- rounded corners used lightly
- crisp icons
- smooth but quiet transitions
- high-quality empty states
- clear status messages

Avoid:

- loud gradients
- busy dashboards
- oversized marketing hero sections inside the app
- cluttered sidebars
- slider walls on first launch
- fake AI magic language
- dark, unreadable interfaces

## Layout Principle

The photo is the main object.

The app should keep the image central and make controls support the image instead
of competing with it.

Recommended desktop structure:

```text
Top toolbar
  import / auto / compare / export

Center
  photo preview and before-after comparison

Right panel
  simple controls first
  advanced sections collapsible

Bottom strip
  thumbnails, status, processing queue
```

## Control Philosophy

Use familiar controls:

- icon buttons for tools
- segmented controls for modes
- sliders for intensity
- toggles for on/off
- menus for profile/look selection
- tabs or collapsible groups for advanced sections

Do not make users read paragraphs inside the app to understand what to do.

## Copywriting

Use short, plain labels:

- Import
- AUTO
- Compare
- Export
- Portrait
- Color
- Film
- Reset

Avoid vague or overpromising labels:

- AI Magic
- Perfect Photo
- One Click Pro
- Beauty Miracle

## Mobile Later

Mobile support is a long-term possibility, not a V0.1 requirement.

Design choices should avoid blocking future mobile use:

- keep workflows simple
- keep controls grouped
- avoid desktop-only mental models where possible
- define responsive information hierarchy early

But do not build the mobile app yet. The priority is a good desktop V0.1.

## First UI Milestone

The first UI milestone should be a simple desktop shell:

```text
Import RAW
  -> show selected file
  -> run AUTO
  -> show processing status
  -> show preview/export path
  -> open output folder
```

It does not need advanced controls yet. It does need to feel clean, calm, and
clear.

## Design Acceptance Criteria

A screen is good enough only if:

- a first-time user knows the next action within a few seconds
- the main photo is visually dominant
- primary actions are obvious
- advanced controls do not clutter the beginner flow
- text fits at desktop and smaller widths
- loading and failure states are clear
- the interface feels polished without pretending unfinished features exist
