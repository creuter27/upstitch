# Shopify Customizer Plugin

A lightweight Shopify theme snippet for live-preview product personalization.
Customers fill in text fields and pick from an image library; a canvas preview
updates in real time. Customizations are saved as Shopify line item properties.

---

## File structure

```
customizer/
├── snippets/
│   └── customizer.liquid      ← Add to your product template
├── assets/
│   ├── customizer.js          ← Storefront canvas logic
│   └── customizer.css         ← Storefront styles
├── admin/
│   ├── index.html             ← Admin tool (open in browser, no server needed)
│   ├── admin.js
│   └── admin.css
└── README.md
```

---

## 1 — Configure with the Admin tool

Open `admin/index.html` in any modern browser (double-click the file).

**Product tab**
1. Enter the product preview image URL (or load a local file for reference).
2. **Draw rectangles** on the canvas by clicking and dragging, or click **+ Add**.
3. Click a rectangle to select it; edit its properties in the bottom panel:
   - **Label** — shown to the customer (e.g. "Name", "Line 2")
   - **Type** — `text` or `image`
   - **Placeholder** — hint text inside the text input
   - **Max characters** — 0 means unlimited

**Global tab**
- **Fonts** — add system fonts (no URL) or hosted custom fonts (URL to `.woff2`).
  Upload font files to *Shopify Admin → Content → Files* first.
- **Colors** — hex values the customer can choose for text rectangles.
- **Image library** — name + URL for each image available in image rectangles.
  Upload images to Shopify Files and paste the CDN URLs here.

**Export tab**
Copy the two JSON blobs and paste them into Shopify metafields (see step 2).

---

## 2 — Create Shopify metafields

Go to **Shopify Admin → Settings → Custom data** and create:

| Owner  | Namespace   | Key               | Type                       |
|--------|-------------|-------------------|----------------------------|
| Shop   | customizer  | global_settings   | JSON                       |
| Product| customizer  | config            | JSON                       |
| Variant| customizer  | preview_image     | Single line text           |

Paste the exported JSON values into the corresponding metafields:
- **Shop → customizer.global_settings** ← "Global settings" JSON from the admin tool
- **Product → customizer.config** ← "Product config" JSON from the admin tool
- **Variant → customizer.preview_image** ← URL of that variant's preview image

To access product/variant metafields:
**Admin → Products → [product] → scroll to Metafields section**

---

## 3 — Add files to your theme

1. Copy `snippets/customizer.liquid` → your theme's `snippets/` folder.
2. Copy `assets/customizer.js` and `assets/customizer.css` → your theme's `assets/` folder.

In **Online Store → Themes → Edit code**, open your product template
(`sections/main-product.liquid` or similar) and add:

```liquid
{% render 'customizer', product: product, variant: product.selected_or_first_available_variant %}
```

Place it just below the product form or wherever you want it to appear.

---

## 4 — Variant change support

When a customer changes variant, the preview image should update.
Most modern themes (Dawn, Impulse, etc.) fire a `variant:changed` custom event.
The snippet already listens for this event and updates the canvas automatically.

If your theme uses a different mechanism, call:

```javascript
window.customizerInstance.updatePreviewImage('https://new-image-url.jpg');
```

---

## Metafield JSON schemas

### `customizer.config` (per product)

```json
{
  "enabled": true,
  "rectangles": [
    {
      "id": "rect1",
      "label": "Name",
      "type": "text",
      "placeholder": "Enter your name…",
      "maxChars": 20,
      "x": 120,
      "y": 200,
      "width": 300,
      "height": 70
    },
    {
      "id": "rect2",
      "label": "Icon",
      "type": "image",
      "x": 50,
      "y": 50,
      "width": 80,
      "height": 80
    }
  ]
}
```

- `x`, `y`, `width`, `height` are in **native image pixels** (as shown in the admin tool).
- Up to 4 rectangles per product.
- `maxChars: 0` means unlimited.

### `customizer.global_settings` (shop-wide)

```json
{
  "fonts": [
    { "name": "Arial",    "url": null },
    { "name": "MyFont",   "url": "https://cdn.shopify.com/files/MyFont.woff2" }
  ],
  "colors": ["#000000", "#ffffff", "#c0392b", "#2980b9"],
  "images": [
    { "name": "Star",  "url": "https://cdn.shopify.com/files/star.png" },
    { "name": "Heart", "url": "https://cdn.shopify.com/files/heart.png" }
  ]
}
```

---

## How order data is stored

Customizations are saved as **Shopify line item properties** when the customer
adds to cart. In the order you will see properties like:

| Property            | Value        |
|---------------------|--------------|
| Name                | Max          |
| Name - Font         | Arial        |
| Name - Color        | #000000      |
| Icon                | Star         |
| Icon - Image URL    | https://…    |

---

## Extending: customer image upload

The code is structured for easy addition of customer image upload later.
In `assets/customizer.js`, find `_buildImageControl()` and add an `<input type="file">`
that reads the selected file as a data-URL and stores it in
`this.values[rect.id].imageUrl`. The render path (`_renderImage`) already handles
any valid image URL, including data-URLs.

---

## Browser support

Chrome 90+, Firefox 88+, Safari 14+, Edge 90+.
Uses: Canvas API, FontFace API, CSS custom properties, Clipboard API.
