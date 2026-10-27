/**
 * Compare view: publication figure export.
 *
 * Python builds the export-styled figure (services/compare_figures.py, with
 * its width in CSS px for the chosen millimetre width) and hands it here. It is
 * drawn off-screen and downloaded straight from the browser by Plotly, so a
 * figure -- which may be built from a visitor's own list -- is never rendered
 * or stored on the server.
 *
 * SVG keeps text as <text>, so labels stay editable in Illustrator/Inkscape.
 * PNG resolution comes from `scale` = dpi / 96.
 */
(function () {
    window.gtCompare = {
        download: async function (figure, options) {
            const mod = await import('nicegui-plotly');
            const Plotly = mod.Plotly;
            const holder = document.createElement('div');
            holder.style.cssText = 'position:fixed;left:-20000px;top:0;';
            document.body.appendChild(holder);
            try {
                await Plotly.newPlot(holder, figure.data, figure.layout, {
                    staticPlot: true,
                    displayModeBar: false,
                });
                await Plotly.downloadImage(holder, {
                    format: options.format,
                    width: figure.layout.width,
                    height: figure.layout.height,
                    scale: options.scale || 1,
                    filename: options.filename,
                });
            } finally {
                Plotly.purge(holder);
                holder.remove();
            }
        },
    };
})();
