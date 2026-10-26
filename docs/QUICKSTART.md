# VoyagerMapper quick start

This guide shows the four steps to make a map file for the Trail Tech Voyager
Pro. The default settings make a file that loads on the device. You do not
have to change them.

Start the app with `run_gui.bat`. Pan and zoom the map to your riding area.

## 1. Select the map and set the opacity

![Layers panel](img/qs1_layers.png)

1. In the **Layers** panel, check the box next to the map you want **(1)**.
   Check one base map, for example *OpenTopoMap* or *USGS Topo*. You can
   also check overlays, for example *USFS MVUM (labeled)*.
2. Drag the slider at the right of the row to set the opacity **(2)**. The map
   view shows the result at once.

![Opacity at 60 percent](img/qs2_opacity.png)

The slider also sets the opacity in the exported file. For a base map, keep
the slider at 100 %. Use a lower value for an overlay that hides the base map.

## 2. Draw the export area

![Draw the export area](img/qs3_area.png)

1. In the **Export to Voyager Pro** panel, click **Draw export rectangle** **(3)**.
2. On the map, press the mouse button at one corner of the area **(4)**.
3. Hold the button, drag to the opposite corner, and release **(5)**.

The app shows the area as a dashed magenta rectangle. The line under the
buttons shows the tile count and the approximate file size **(6)**. A thin
dotted outline shows the area snapped to the tile grid. That is the area the
app exports.

To change the area, click **Draw export rectangle** again and draw a new
rectangle. **Clear** removes the area.

## 3. Export the map

![Export settings](img/qs4_export.png)

1. Type a name in **Map name** **(7)**. The Voyager Pro shows this name in its
   list of custom maps.
2. Keep the **Zoom levels** at the default, 8 to 14 **(8)**. More zoom levels
   make a larger file. Each level above 14 makes the file four times larger.
3. Click **Export MBTiles...** **(9)**.
4. In the file dialog, choose where to save the `.mbtiles` file and click
   **Save**.

![Export progress](img/qs5_progress.png)

The top bar shows the overall progress **(10)**. The bottom bar shows the
current step. The export downloads the tiles for each layer, then writes the
file. A message box opens when the export is complete. **Cancel** stops the
export.

## 4. Load the map on the Voyager Pro

1. Copy the `.mbtiles` file to the microSD card of the Voyager Pro.
2. Put the card in the Voyager Pro.
3. On the device, go to the map screen.
4. Tap **CUSTOM MAPS**.
5. Tap **IMPORT A NEW FILE** and select the file.

The device processes the file and shows a message when it is done.

## Tips

- The app keeps the downloaded tiles in a cache. A second export of the same
  area, for example a night version, does not download the tiles again.
- Select **Night** in **Theme** to make a dark map. Check **export a day +
  night pair** to make both files in one export.
- **File → Save project** saves the area, the layers, and the export
  settings, so you can make the same map again later.
