/* AIS Source Merge map overlay for AvNav 20261102. */
export default (api) => {
  const POLL_MS = 1000;

  const drawLabel = (ctx, x, y, text, color) => {
    const fontSize = 11;
    const padX = 4;
    const height = 18;
    const left = x + 10;
    const top = y - height - 8;
    ctx.save();
    ctx.font = `600 ${fontSize}px Segoe UI,Arial,sans-serif`;
    ctx.textBaseline = 'middle';
    const width = ctx.measureText(text).width + 2 * padX;
    ctx.fillStyle = 'rgba(18,27,35,0.88)';
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.rect(left, top, width, height);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.fillText(text, left + padX, top + height / 2);
    ctx.restore();
  };

  const widget = {
    name: 'AIS reception source',
    type: 'map',
    storeKeys: {},

    initFunction: function (context) {
      context.aisSourceTargets = [];
      context.aisSourceStopped = false;
      const poll = async () => {
        if (context.aisSourceStopped) return;
        try {
          const response = await fetch(new URL('api', import.meta.url).toString(), {cache: 'no-store'});
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const data = await response.json();
          context.aisSourceTargets = Array.isArray(data.targets) ? data.targets : [];
          context.triggerRedraw();
        }
        catch (error) {
          api.log(`AIS Source Merge API error: ${error}`);
        }
        if (!context.aisSourceStopped) {
          context.aisSourceTimer = window.setTimeout(poll, POLL_MS);
        }
      };
      poll();
    },

    finalizeFunction: function (context) {
      context.aisSourceStopped = true;
      if (context.aisSourceTimer) window.clearTimeout(context.aisSourceTimer);
    },

    renderCanvas: function (canvas) {
      const ctx = canvas.getContext('2d');
      const targets = this.aisSourceTargets || [];
      for (const target of targets) {
        if (typeof target.lat !== 'number' || typeof target.lon !== 'number') continue;
        const pixel = this.lonLatToPixel(target.lon, target.lat);
        if (!pixel || pixel.length < 2) continue;
        const active = target.active || [];
        const color = active.length > 1 ? '#72e6a6' :
          active.length === 1 ? '#ffd36b' : '#aab2b9';
        drawLabel(ctx, pixel[0], pixel[1], target.source || 'unknown', color);
      }
    }
  };

  api.registerWidget(widget);
};
