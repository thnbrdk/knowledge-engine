/* ═══════════════════════════════════════════════════════════════════════════
   Brain Bubble Diagram — Force-directed category visualization
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
    'use strict';

    var canvas = document.getElementById('bubble-canvas');
    if (!canvas) return;

    var ctx = canvas.getContext('2d');
    var container = canvas.parentElement;
    var dpr = window.devicePixelRatio || 1;
    var nodes = [];
    var edges = [];
    var animationId = null;
    var hoveredNode = null;
    var draggingNode = null;
    var dragOffsetX = 0;
    var dragOffsetY = 0;
    var W, H;

    // Theme colors
    var COLORS = [
        '#22c55e', // green
        '#3b82f6', // blue
        '#a855f7', // purple
        '#f97316', // orange
        '#eab308', // yellow
        '#ec4899', // pink
        '#14b8a6', // teal
        '#ef4444', // red
        '#6366f1', // indigo
        '#84cc16', // lime
    ];

    function resize() {
        var rect = container.getBoundingClientRect();
        W = rect.width;
        H = 420;
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        canvas.style.width = W + 'px';
        canvas.style.height = H + 'px';
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function initNodes(categories, overlaps) {
        if (!categories || categories.length === 0) return;

        // Find max doc count for sizing
        var maxCount = 1;
        for (var i = 0; i < categories.length; i++) {
            if (categories[i].doc_count > maxCount) maxCount = categories[i].doc_count;
        }

        var minRadius = 30;
        var maxRadius = 70;

        // Create nodes
        nodes = [];
        for (var i = 0; i < categories.length; i++) {
            var cat = categories[i];
            var ratio = cat.doc_count / maxCount;
            var r = minRadius + ratio * (maxRadius - minRadius);
            nodes.push({
                id: cat.category,
                label: cat.category,
                count: cat.doc_count,
                r: r,
                x: W / 2 + (Math.random() - 0.5) * W * 0.5,
                y: H / 2 + (Math.random() - 0.5) * H * 0.4,
                vx: 0,
                vy: 0,
                color: COLORS[i % COLORS.length],
            });
        }

        // Build lookup
        var nodeMap = {};
        for (var i = 0; i < nodes.length; i++) {
            nodeMap[nodes[i].id] = nodes[i];
        }

        // Create edges from overlaps
        edges = [];
        if (overlaps) {
            for (var i = 0; i < overlaps.length; i++) {
                var ov = overlaps[i];
                var src = nodeMap[ov.source];
                var tgt = nodeMap[ov.target];
                if (src && tgt) {
                    edges.push({ source: src, target: tgt, shared: ov.shared });
                }
            }
        }
    }

    function simulate() {
        var damping = 0.85;
        var centerForce = 0.002;
        var repulsion = 2500;
        var attraction = 0.015;

        for (var i = 0; i < nodes.length; i++) {
            var n = nodes[i];
            if (n === draggingNode) continue;

            // Pull toward center
            n.vx += (W / 2 - n.x) * centerForce;
            n.vy += (H / 2 - n.y) * centerForce;

            // Repel from other nodes
            for (var j = i + 1; j < nodes.length; j++) {
                var m = nodes[j];
                var dx = n.x - m.x;
                var dy = n.y - m.y;
                var dist = Math.sqrt(dx * dx + dy * dy) || 1;
                var minDist = n.r + m.r + 10;
                if (dist < minDist * 2) {
                    var force = repulsion / (dist * dist);
                    var fx = dx / dist * force;
                    var fy = dy / dist * force;
                    n.vx += fx;
                    n.vy += fy;
                    if (m !== draggingNode) {
                        m.vx -= fx;
                        m.vy -= fy;
                    }
                }
            }
        }

        // Attract connected nodes (overlapping categories)
        for (var i = 0; i < edges.length; i++) {
            var e = edges[i];
            var dx = e.target.x - e.source.x;
            var dy = e.target.y - e.source.y;
            var dist = Math.sqrt(dx * dx + dy * dy) || 1;
            // Target distance: bubbles overlap proportionally to shared docs
            var overlap = Math.min(e.shared * 15, (e.source.r + e.target.r) * 0.5);
            var idealDist = e.source.r + e.target.r - overlap;
            var delta = (dist - idealDist) * attraction * (1 + e.shared * 0.5);
            var fx = dx / dist * delta;
            var fy = dy / dist * delta;
            if (e.source !== draggingNode) {
                e.source.vx += fx;
                e.source.vy += fy;
            }
            if (e.target !== draggingNode) {
                e.target.vx -= fx;
                e.target.vy -= fy;
            }
        }

        // Apply velocity
        for (var i = 0; i < nodes.length; i++) {
            var n = nodes[i];
            if (n === draggingNode) continue;
            n.vx *= damping;
            n.vy *= damping;
            n.x += n.vx;
            n.y += n.vy;
            // Keep in bounds
            n.x = Math.max(n.r, Math.min(W - n.r, n.x));
            n.y = Math.max(n.r, Math.min(H - n.r, n.y));
        }
    }

    function draw() {
        ctx.clearRect(0, 0, W, H);

        // Draw connection lines for overlapping categories
        for (var i = 0; i < edges.length; i++) {
            var e = edges[i];
            ctx.beginPath();
            ctx.moveTo(e.source.x, e.source.y);
            ctx.lineTo(e.target.x, e.target.y);
            ctx.strokeStyle = 'rgba(34, 197, 94, 0.15)';
            ctx.lineWidth = Math.max(1, e.shared * 2);
            ctx.stroke();
        }

        // Draw nodes
        for (var i = 0; i < nodes.length; i++) {
            var n = nodes[i];
            var isHovered = n === hoveredNode;

            // Outer glow
            if (isHovered) {
                ctx.beginPath();
                ctx.arc(n.x, n.y, n.r + 6, 0, Math.PI * 2);
                ctx.fillStyle = n.color.replace(')', ', 0.15)').replace('rgb', 'rgba').replace('#', '');
                // Use hex glow
                ctx.shadowColor = n.color;
                ctx.shadowBlur = 20;
                ctx.fill();
                ctx.shadowBlur = 0;
            }

            // Main circle
            ctx.beginPath();
            ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
            var alpha = isHovered ? 0.25 : 0.12;
            ctx.fillStyle = hexToRgba(n.color, alpha);
            ctx.fill();
            ctx.strokeStyle = isHovered ? n.color : hexToRgba(n.color, 0.5);
            ctx.lineWidth = isHovered ? 2 : 1;
            ctx.stroke();

            // Pulsing inner glow
            var pulse = 0.03 + Math.sin(Date.now() * 0.002 + i) * 0.02;
            var gradient = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r);
            gradient.addColorStop(0, hexToRgba(n.color, pulse + 0.08));
            gradient.addColorStop(1, hexToRgba(n.color, 0));
            ctx.beginPath();
            ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
            ctx.fillStyle = gradient;
            ctx.fill();

            // Label
            ctx.fillStyle = isHovered ? '#ffffff' : '#e5e5e5';
            ctx.font = (isHovered ? 'bold ' : '') + '11px JetBrains Mono, monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';

            // Truncate label if too wide
            var label = n.label;
            var maxTextWidth = n.r * 1.6;
            if (ctx.measureText(label).width > maxTextWidth) {
                while (label.length > 3 && ctx.measureText(label + '…').width > maxTextWidth) {
                    label = label.slice(0, -1);
                }
                label += '…';
            }
            ctx.fillText(label, n.x, n.y - 6);

            // Count
            ctx.fillStyle = hexToRgba(n.color, 0.8);
            ctx.font = '10px JetBrains Mono, monospace';
            ctx.fillText(n.count + ' doc' + (n.count !== 1 ? 's' : ''), n.x, n.y + 8);
        }

        // Tooltip for hovered node
        if (hoveredNode) {
            canvas.style.cursor = 'pointer';
        } else {
            canvas.style.cursor = 'default';
        }
    }

    function hexToRgba(hex, alpha) {
        var r = parseInt(hex.slice(1, 3), 16);
        var g = parseInt(hex.slice(3, 5), 16);
        var b = parseInt(hex.slice(5, 7), 16);
        return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
    }

    function getMousePos(e) {
        var rect = canvas.getBoundingClientRect();
        return {
            x: e.clientX - rect.left,
            y: e.clientY - rect.top,
        };
    }

    function findNode(pos) {
        for (var i = nodes.length - 1; i >= 0; i--) {
            var n = nodes[i];
            var dx = pos.x - n.x;
            var dy = pos.y - n.y;
            if (dx * dx + dy * dy <= n.r * n.r) return n;
        }
        return null;
    }

    // Mouse events
    canvas.addEventListener('mousemove', function (e) {
        var pos = getMousePos(e);
        if (draggingNode) {
            draggingNode.x = pos.x - dragOffsetX;
            draggingNode.y = pos.y - dragOffsetY;
            draggingNode.vx = 0;
            draggingNode.vy = 0;
            return;
        }
        hoveredNode = findNode(pos);
    });

    canvas.addEventListener('mousedown', function (e) {
        var pos = getMousePos(e);
        var node = findNode(pos);
        if (node) {
            draggingNode = node;
            dragOffsetX = pos.x - node.x;
            dragOffsetY = pos.y - node.y;
            e.preventDefault();
        }
    });

    canvas.addEventListener('mouseup', function () {
        draggingNode = null;
    });

    canvas.addEventListener('mouseleave', function () {
        hoveredNode = null;
        draggingNode = null;
    });

    // Track drag to prevent click-navigation after dragging
    var wasDragging = false;
    canvas.addEventListener('mousedown', function () { wasDragging = false; });
    canvas.addEventListener('mousemove', function () {
        if (draggingNode) wasDragging = true;
    });

    canvas.addEventListener('click', function (e) {
        if (wasDragging) {
            wasDragging = false;
            return;
        }
        var pos = getMousePos(e);
        var node = findNode(pos);
        if (node) {
            window.location.href = '/category/' + encodeURIComponent(node.id);
        }
    });

    function tick() {
        simulate();
        draw();
        animationId = requestAnimationFrame(tick);
    }

    // Load data and start
    function init() {
        resize();

        fetch('/api/category-graph')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                initNodes(data.categories, data.overlaps);
                tick();
            })
            .catch(function (err) {
                console.error('Failed to load category graph:', err);
            });
    }

    window.addEventListener('resize', function () {
        resize();
    });

    // Start when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
