(() => {
    const canvas = document.getElementById("gameCanvas");
    const ctx = canvas.getContext("2d");
    const introOverlay = document.getElementById("introOverlay");
    const finalOverlay = document.getElementById("finalOverlay");
    const acceptedOverlay = document.getElementById("acceptedOverlay");
    const startBtn = document.getElementById("startBtn");
    const yesBtn = document.getElementById("yesBtn");
    const noBtn = document.getElementById("noBtn");
    const finalActions = document.getElementById("finalActions");

    const WIDTH = canvas.width;
    const HEIGHT = canvas.height;
    const GROUND_Y = 430;
    const GRAVITY = 1800;
    const FIXED_DT = 1 / 60;

    const keys = {
        left: false,
        right: false,
        up: false,
        down: false,
    };

    const levels = [
        {
            id: 1,
            city: "Boston",
            collectibleLabel: "Roses",
            collectibleColor: "#d81b60",
            sky: ["#b3e5fc", "#fff8e1"],
            skyline: [
                { x: 50, w: 70, h: 100 },
                { x: 140, w: 95, h: 140 },
                { x: 260, w: 80, h: 110 },
                { x: 360, w: 120, h: 170 },
                { x: 520, w: 90, h: 130 },
            ],
            puppyColor: "#69a8ff",
            puppyAccent: "#2f5ea8",
            worldWidth: 2500,
            required: 5,
            obstacles: [
                { type: "ground", x: 360, y: GROUND_Y - 30, w: 60, h: 30 },
                { type: "ground", x: 650, y: GROUND_Y - 45, w: 70, h: 45 },
                { type: "arch", x: 930, y: GROUND_Y - 118, w: 90, h: 58 },
                { type: "ground", x: 1240, y: GROUND_Y - 32, w: 80, h: 32 },
                { type: "ground", x: 1550, y: GROUND_Y - 55, w: 90, h: 55 },
                { type: "arch", x: 1860, y: GROUND_Y - 112, w: 94, h: 54 },
                { type: "ground", x: 2140, y: GROUND_Y - 36, w: 70, h: 36 },
            ],
            collectibles: [
                { x: 300, y: GROUND_Y - 120 },
                { x: 720, y: GROUND_Y - 160 },
                { x: 980, y: GROUND_Y - 72 },
                { x: 1430, y: GROUND_Y - 130 },
                { x: 1730, y: GROUND_Y - 170 },
                { x: 2200, y: GROUND_Y - 100 },
            ],
            puppyX: 2320,
        },
        {
            id: 2,
            city: "Philadelphia",
            collectibleLabel: "Chocolates",
            collectibleColor: "#6d3f24",
            sky: ["#d1c4e9", "#ffecb3"],
            skyline: [
                { x: 40, w: 100, h: 140 },
                { x: 170, w: 120, h: 160 },
                { x: 320, w: 90, h: 120 },
                { x: 450, w: 110, h: 190 },
                { x: 600, w: 120, h: 150 },
            ],
            puppyColor: "#ffb866",
            puppyAccent: "#9a6324",
            worldWidth: 2650,
            required: 5,
            obstacles: [
                { type: "moving", x: 420, y: GROUND_Y - 32, w: 70, h: 32, range: 120, speed: 1.2 },
                { type: "arch", x: 820, y: GROUND_Y - 108, w: 96, h: 52 },
                { type: "ground", x: 1120, y: GROUND_Y - 48, w: 82, h: 48 },
                { type: "moving", x: 1450, y: GROUND_Y - 34, w: 74, h: 34, range: 140, speed: 1.45 },
                { type: "arch", x: 1760, y: GROUND_Y - 110, w: 90, h: 54 },
                { type: "ground", x: 2020, y: GROUND_Y - 58, w: 90, h: 58 },
                { type: "moving", x: 2320, y: GROUND_Y - 38, w: 70, h: 38, range: 90, speed: 1.5 },
            ],
            collectibles: [
                { x: 320, y: GROUND_Y - 90 },
                { x: 680, y: GROUND_Y - 170 },
                { x: 905, y: GROUND_Y - 70 },
                { x: 1320, y: GROUND_Y - 150 },
                { x: 1680, y: GROUND_Y - 70 },
                { x: 2100, y: GROUND_Y - 140 },
                { x: 2480, y: GROUND_Y - 90 },
            ],
            puppyX: 2440,
        },
        {
            id: 3,
            city: "London",
            collectibleLabel: "Love Letters",
            collectibleColor: "#f06292",
            sky: ["#b2dfdb", "#fce4ec"],
            skyline: [
                { x: 40, w: 75, h: 140 },
                { x: 130, w: 55, h: 210 },
                { x: 230, w: 120, h: 150 },
                { x: 380, w: 95, h: 130 },
                { x: 500, w: 150, h: 170 },
            ],
            puppyColor: "#9de17a",
            puppyAccent: "#3f7d2f",
            worldWidth: 2800,
            required: 6,
            obstacles: [
                { type: "ground", x: 360, y: GROUND_Y - 38, w: 74, h: 38 },
                { type: "moving", x: 660, y: GROUND_Y - 30, w: 64, h: 30, range: 120, speed: 1.55 },
                { type: "arch", x: 980, y: GROUND_Y - 110, w: 88, h: 55 },
                { type: "ground", x: 1250, y: GROUND_Y - 64, w: 98, h: 64 },
                { type: "moving", x: 1560, y: GROUND_Y - 34, w: 74, h: 34, range: 130, speed: 1.65 },
                { type: "arch", x: 1860, y: GROUND_Y - 116, w: 92, h: 57 },
                { type: "ground", x: 2140, y: GROUND_Y - 52, w: 86, h: 52 },
                { type: "moving", x: 2410, y: GROUND_Y - 40, w: 78, h: 40, range: 120, speed: 1.75 },
            ],
            collectibles: [
                { x: 300, y: GROUND_Y - 100 },
                { x: 620, y: GROUND_Y - 170 },
                { x: 1020, y: GROUND_Y - 74 },
                { x: 1380, y: GROUND_Y - 180 },
                { x: 1700, y: GROUND_Y - 76 },
                { x: 2060, y: GROUND_Y - 160 },
                { x: 2360, y: GROUND_Y - 84 },
                { x: 2640, y: GROUND_Y - 128 },
            ],
            puppyX: 2600,
        },
    ];

    const game = {
        mode: "intro",
        levelIndex: 0,
        levelTime: 0,
        retries: 0,
        totalCollected: 0,
        message: "",
        transitionTimer: 0,
        transitionText: "",
        cameraX: 0,
        noButtonPos: { x: 0, y: 0 },
        confetti: [],
        confettiTimer: 0,
    };

    const player = {
        x: 100,
        y: GROUND_Y - 64,
        vx: 0,
        vy: 0,
        w: 38,
        h: 64,
        slideH: 38,
        face: 1,
        grounded: true,
        isSliding: false,
    };

    let activeLevel = null;

    function cloneLevel(levelData) {
        return {
            ...levelData,
            obstacles: levelData.obstacles.map((o) => ({ ...o, baseX: o.x, phase: Math.random() * Math.PI * 2 })),
            collectibles: levelData.collectibles.map((c, i) => ({ id: i + 1, ...c, collected: false })),
        };
    }

    function rectsIntersect(a, b) {
        return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
    }

    function currentLevelMode() {
        return `level_${game.levelIndex + 1}`;
    }

    function setMode(mode) {
        game.mode = mode;
        introOverlay.hidden = mode !== "intro";
        finalOverlay.hidden = mode !== "final_question";
        acceptedOverlay.hidden = mode !== "accepted";
    }

    function resetPlayer() {
        player.x = 100;
        player.y = GROUND_Y - player.h;
        player.vx = 0;
        player.vy = 0;
        player.grounded = true;
        player.isSliding = false;
    }

    function startLevel(index) {
        game.levelIndex = index;
        game.levelTime = 0;
        game.cameraX = 0;
        game.transitionTimer = 0;
        game.transitionText = "";
        activeLevel = cloneLevel(levels[index]);
        activeLevel.collectedCount = 0;
        resetPlayer();
        setMode(currentLevelMode());
    }

    function beginGame() {
        game.retries = 0;
        game.totalCollected = 0;
        startLevel(0);
    }

    function triggerLevelComplete() {
        if (game.transitionTimer > 0) return;
        game.transitionTimer = 1.7;
        game.transitionText = `Level ${activeLevel.id} complete, Ashi!`;
    }

    function goToFinalQuestion() {
        setMode("final_question");
        // Initial position for No button
        game.noButtonPos.x = 350;
        game.noButtonPos.y = 0;
        noBtn.style.left = `${game.noButtonPos.x}px`;
        noBtn.style.top = `${game.noButtonPos.y}px`;
    }

    function acceptValentine() {
        setMode("accepted");
        game.confettiTimer = 5.0;
        game.confetti = Array.from({ length: 150 }, () => ({
            x: Math.random() * WIDTH,
            y: -Math.random() * HEIGHT,
            vx: (Math.random() - 0.5) * 200,
            vy: 150 + Math.random() * 300,
            size: 6 + Math.random() * 8,
            color: ["#ff4f87", "#ffd166", "#06d6a0", "#4cc9f0", "#f72585"][Math.floor(Math.random() * 5)],
            spin: (Math.random() - 0.5) * 10,
            rot: Math.random() * Math.PI,
        }));
    }

    function playerRect() {
        const h = player.isSliding ? player.slideH : player.h;
        return {
            x: player.x,
            y: player.y + (player.h - h),
            w: player.w,
            h,
        };
    }

    function updatePlayer(dt) {
        const moveSpeed = 300;
        const accel = 2000;
        const friction = 2000;

        let desired = 0;
        if (keys.left) desired -= moveSpeed;
        if (keys.right) desired += moveSpeed;

        if (desired !== 0) {
            if (player.vx < desired) player.vx = Math.min(desired, player.vx + accel * dt);
            else if (player.vx > desired) player.vx = Math.max(desired, player.vx - accel * dt);
        } else {
            if (player.vx > 0) player.vx = Math.max(0, player.vx - friction * dt);
            if (player.vx < 0) player.vx = Math.min(0, player.vx + friction * dt);
        }

        if (player.vx > 5) player.face = 1;
        else if (player.vx < -5) player.face = -1;

        if (keys.up && player.grounded) {
            player.vy = -750;
            player.grounded = false;
        }

        player.isSliding = keys.down && player.grounded;

        player.vy += GRAVITY * dt;
        player.x += player.vx * dt;
        player.y += player.vy * dt;

        if (player.x < 0) player.x = 0;
        const rightLimit = activeLevel.worldWidth - player.w;
        if (player.x > rightLimit) player.x = rightLimit;

        const floorY = GROUND_Y - player.h;
        if (player.y >= floorY) {
            player.y = floorY;
            player.vy = 0;
            player.grounded = true;
        }
    }

    function updateLevel(dt) {
        if (!activeLevel) return;

        game.levelTime += dt;
        updatePlayer(dt);

        for (const ob of activeLevel.obstacles) {
            if (ob.type === "moving") {
                ob.phase += dt * ob.speed;
                ob.x = ob.baseX + Math.sin(ob.phase) * ob.range;
            }
        }

        const pRect = playerRect();

        for (const ob of activeLevel.obstacles) {
            const oRect = { x: ob.x, y: ob.y, w: ob.w, h: ob.h };
            if (rectsIntersect(pRect, oRect)) {
                game.retries += 1;
                resetPlayer();
                return;
            }
        }

        for (const c of activeLevel.collectibles) {
            if (c.collected) continue;
            const cRect = { x: c.x - 15, y: c.y - 15, w: 30, h: 30 };
            if (rectsIntersect(pRect, cRect)) {
                c.collected = true;
                activeLevel.collectedCount += 1;
                game.totalCollected += 1;
            }
        }

        const gateX = activeLevel.worldWidth - 150;
        const canFinish = activeLevel.collectedCount >= activeLevel.required;
        if (canFinish && player.x + player.w >= gateX) {
            triggerLevelComplete();
        }

        const targetCameraX = Math.max(0, Math.min(activeLevel.worldWidth - WIDTH, player.x - WIDTH * 0.4));
        game.cameraX += (targetCameraX - game.cameraX) * 0.1;

        if (game.transitionTimer > 0) {
            game.transitionTimer -= dt;
            if (game.transitionTimer <= 0) {
                if (game.levelIndex < levels.length - 1) {
                    startLevel(game.levelIndex + 1);
                } else {
                    goToFinalQuestion();
                }
            }
        }
    }

    function updateFinalQuestion() {
        const rect = finalActions.getBoundingClientRect();
        const noRect = noBtn.getBoundingClientRect();

        // Use client coordinates for cursor relative to the shell
        const shellRect = document.querySelector('.game-shell').getBoundingClientRect();
        const cursor = updateFinalQuestion.lastCursor;

        if (cursor) {
            // Convert cursor to relative shell coords
            const relX = cursor.x - shellRect.left;
            const relY = cursor.y - shellRect.top;

            // Get center of No button in relative shell coords
            const noCenterX = game.noButtonPos.x + noRect.width / 2 + rect.left - shellRect.left;
            const noCenterY = game.noButtonPos.y + noRect.height / 2 + rect.top - shellRect.top;

            const dx = noCenterX - relX;
            const dy = noCenterY - relY;
            const dist = Math.hypot(dx, dy);

            if (dist < 180) { // Increased trigger radius
                const force = (180 - dist) / 180;
                const pushX = (dx / dist) * force * 150;
                const pushY = (dy / dist) * force * 150;

                game.noButtonPos.x += pushX;
                game.noButtonPos.y += pushY;
            }
        }

        // Clamp inside actions container with more breathing room
        let x = game.noButtonPos.x;
        let y = game.noButtonPos.y;

        x = Math.max(-50, Math.min(rect.width + 50, x));
        y = Math.max(-80, Math.min(rect.height + 80, y));

        game.noButtonPos.x = x;
        game.noButtonPos.y = y;
        noBtn.style.left = `${x}px`;
        noBtn.style.top = `${y}px`;
    }

    function drawBackground(level) {
        const grad = ctx.createLinearGradient(0, 0, 0, HEIGHT);
        grad.addColorStop(0, level.sky[0]);
        grad.addColorStop(1, level.sky[1]);
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, WIDTH, HEIGHT);

        const cityOffset = game.cameraX * 0.3;
        ctx.fillStyle = "rgba(47, 29, 39, 0.12)";

        // City Specific Landmarks
        if (level.city === "Boston") {
            // Prudential Center silhouette
            const pruX = 400 - cityOffset;
            ctx.fillRect(pruX, GROUND_Y - 240, 60, 240);
            ctx.fillRect(pruX + 10, GROUND_Y - 260, 40, 20);

            // Zakim Bridge
            ctx.strokeStyle = "rgba(47, 29, 39, 0.1)";
            ctx.lineWidth = 2;
            ctx.beginPath();
            const bridgeX = 800 - cityOffset;
            ctx.moveTo(bridgeX, GROUND_Y);
            ctx.lineTo(bridgeX + 40, GROUND_Y - 120);
            ctx.lineTo(bridgeX + 80, GROUND_Y);
            ctx.stroke();
            for (let i = 1; i < 6; i++) {
                ctx.beginPath();
                ctx.moveTo(bridgeX + 40, GROUND_Y - 120);
                ctx.lineTo(bridgeX + 40 + (i * 15), GROUND_Y - (i * 5));
                ctx.stroke();
            }

            // Brownstones
            ctx.fillStyle = "rgba(121, 85, 72, 0.15)";
            for (let i = 0; i < 3; i++) {
                ctx.fillRect(150 + (i * 200) - cityOffset, GROUND_Y - 100, 80, 100);
                ctx.fillStyle = "rgba(255,255,255,0.1)";
                ctx.fillRect(160 + (i * 200) - cityOffset, GROUND_Y - 80, 20, 30);
                ctx.fillRect(200 + (i * 200) - cityOffset, GROUND_Y - 80, 20, 30);
                ctx.fillStyle = "rgba(121, 85, 72, 0.15)";
            }
        } else if (level.city === "Philadelphia") {
            // Philadelphia Museum of Art (Rocky Steps)
            const museumX = 450 - cityOffset;
            ctx.fillStyle = "rgba(47, 29, 39, 0.12)";
            ctx.fillRect(museumX, GROUND_Y - 100, 240, 100);
            ctx.fillRect(museumX + 80, GROUND_Y - 140, 80, 40);
            // Steps
            for (let i = 0; i < 5; i++) {
                ctx.fillRect(museumX + 20, GROUND_Y - (i * 8), 200, 2);
            }

            // One Liberty Place
            const libertyX = 850 - cityOffset;
            ctx.fillRect(libertyX, GROUND_Y - 280, 60, 280);
            ctx.beginPath();
            ctx.moveTo(libertyX, GROUND_Y - 280);
            ctx.lineTo(libertyX + 30, GROUND_Y - 320);
            ctx.lineTo(libertyX + 60, GROUND_Y - 280);
            ctx.fill();

            // City Hall (Comcast Tower in background)
            ctx.fillRect(1100 - cityOffset, GROUND_Y - 300, 80, 300);
        } else if (level.city === "London") {
            // Big Ben
            const benX = 400 - cityOffset;
            ctx.fillStyle = "rgba(47, 29, 39, 0.12)";
            ctx.fillRect(benX, GROUND_Y - 220, 45, 220);
            ctx.fillRect(benX - 5, GROUND_Y - 230, 55, 15);
            ctx.beginPath();
            ctx.moveTo(benX, GROUND_Y - 230);
            ctx.lineTo(benX + 22, GROUND_Y - 260);
            ctx.lineTo(benX + 45, GROUND_Y - 230);
            ctx.fill();
            // Clock face
            ctx.fillStyle = "rgba(255, 255, 255, 0.3)";
            ctx.beginPath();
            ctx.arc(benX + 22, GROUND_Y - 200, 12, 0, Math.PI * 2);
            ctx.fill();

            // The Shard
            const shardX = 650 - cityOffset;
            ctx.fillStyle = "rgba(47, 29, 39, 0.1)";
            ctx.beginPath();
            ctx.moveTo(shardX, GROUND_Y);
            ctx.lineTo(shardX + 30, GROUND_Y - 340);
            ctx.lineTo(shardX + 60, GROUND_Y);
            ctx.fill();

            // London Eye
            ctx.strokeStyle = "rgba(40, 40, 70, 0.15)";
            ctx.lineWidth = 4;
            const wheelX = 950 - cityOffset;
            const wheelY = 220;
            const r = 90;
            ctx.beginPath();
            ctx.arc(wheelX, wheelY, r, 0, Math.PI * 2);
            ctx.stroke();
            for (let i = 0; i < 16; i++) {
                const ang = (Math.PI * 2 * i) / 16;
                ctx.beginPath();
                ctx.moveTo(wheelX, wheelY);
                ctx.lineTo(wheelX + Math.cos(ang) * r, wheelY + Math.sin(ang) * r);
                ctx.stroke();
            }
        }

        ctx.fillStyle = "rgba(47, 29, 39, 0.08)";
        for (const b of level.skyline) {
            const bx = b.x * 4 - cityOffset;
            const bw = b.w;
            const by = GROUND_Y - b.h;
            ctx.fillRect(bx, by, bw, b.h);
        }

        ctx.fillStyle = "#66bb6a";
        ctx.fillRect(0, GROUND_Y, WIDTH, HEIGHT - GROUND_Y);

        // Ground details
        ctx.fillStyle = "#43a047";
        for (let i = -100; i < WIDTH + 100; i += 40) {
            const x = i - (game.cameraX % 40);
            ctx.fillRect(x, GROUND_Y, 20, 4);
        }
    }

    function drawHUD(level) {
        ctx.save();
        const hudW = 340;
        const hudX = WIDTH - hudW - 20;
        const hudY = 20;

        ctx.fillStyle = "rgba(255, 255, 255, 0.88)";
        ctx.shadowColor = "rgba(0,0,0,0.1)";
        ctx.shadowBlur = 15;
        ctx.beginPath();
        ctx.roundRect(hudX, hudY, hudW, 100, 16);
        ctx.fill();
        ctx.restore();

        ctx.fillStyle = "#2f1d27";
        ctx.font = "700 22px 'Outfit', sans-serif";
        ctx.fillText(`${level.city} Quest`, hudX + 20, hudY + 35);

        ctx.font = "600 15px 'Inter', sans-serif";
        ctx.fillStyle = "rgba(47, 29, 39, 0.75)";
        ctx.fillText(`${level.collectibleLabel}: ${level.collectedCount} / ${level.required}`, hudX + 20, hudY + 65);
        ctx.fillText(`Deaths: ${game.retries}`, hudX + 20, hudY + 85);

        // Progress bar
        const barW = 120;
        const barX = hudX + hudW - barW - 20;
        ctx.fillStyle = "rgba(47, 29, 39, 0.1)";
        ctx.fillRect(barX, hudY + 55, barW, 10);
        const progress = Math.min(1, level.collectedCount / level.required);
        ctx.fillStyle = "#ff4f87";
        ctx.fillRect(barX, hudY + 55, barW * progress, 10);
    }

    function drawGate(level) {
        const gateX = level.worldWidth - 150 - game.cameraX;
        ctx.save();
        ctx.fillStyle = "#5d4037";
        ctx.fillRect(gateX, GROUND_Y - 140, 24, 140);

        const open = level.collectedCount >= level.required;
        ctx.fillStyle = open ? "#2fbf71" : "#ff5f6d";
        ctx.beginPath();
        ctx.roundRect(gateX - 20, GROUND_Y - 130, 64, 40, 8);
        ctx.fill();

        ctx.fillStyle = "#fff";
        ctx.font = "bold 12px 'Inter', sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(open ? "GO!" : "STOP", gateX + 12, GROUND_Y - 105);
        ctx.restore();
    }

    function drawObstacles(level) {
        for (const ob of level.obstacles) {
            const x = ob.x - game.cameraX;
            if (x + ob.w < -50 || x > WIDTH + 50) continue;

            ctx.save();
            if (ob.type === "arch") {
                ctx.fillStyle = "#b71c1c";
            } else if (ob.type === "moving") {
                ctx.fillStyle = "#6a1b9a";
            } else {
                ctx.fillStyle = "#4e342e";
            }

            ctx.beginPath();
            ctx.roundRect(x, ob.y, ob.w, ob.h, 6);
            ctx.fill();

            // Detail highlight
            ctx.fillStyle = "rgba(255, 255, 255, 0.2)";
            ctx.fillRect(x + 4, ob.y + 4, ob.w - 8, 4);
            ctx.restore();
        }
    }

    function drawCollectibles(level) {
        for (const c of level.collectibles) {
            if (c.collected) continue;
            const x = c.x - game.cameraX;
            if (x < -30 || x > WIDTH + 30) continue;

            const bounce = Math.sin(Date.now() / 200) * 5;
            const cy = c.y + bounce;

            ctx.save();
            ctx.fillStyle = level.collectibleColor;
            ctx.shadowBlur = 15;
            ctx.shadowColor = level.collectibleColor;

            if (level.id === 1) { // Rose
                ctx.beginPath();
                ctx.arc(x - 6, cy - 4, 8, 0, Math.PI * 2);
                ctx.arc(x + 6, cy - 4, 8, 0, Math.PI * 2);
                ctx.arc(x, cy + 4, 8, 0, Math.PI * 2);
                ctx.fill();
                ctx.fillStyle = "#2e7d32";
                ctx.fillRect(x - 2, cy + 8, 4, 12);
            } else if (level.id === 2) { // Chocolate
                ctx.beginPath();
                ctx.roundRect(x - 12, cy - 12, 24, 24, 4);
                ctx.fill();
                ctx.fillStyle = "rgba(255, 255, 255, 0.2)";
                ctx.fillRect(x - 8, cy - 8, 6, 6);
            } else { // Love Letter
                ctx.beginPath();
                ctx.roundRect(x - 15, cy - 10, 30, 20, 2);
                ctx.fill();
                ctx.fillStyle = "#fff";
                ctx.beginPath();
                ctx.moveTo(x - 15, cy - 10);
                ctx.lineTo(x, cy);
                ctx.lineTo(x + 15, cy - 10);
                ctx.stroke();
            }
            ctx.restore();
        }
    }

    function drawPuppy(level) {
        const x = level.puppyX - game.cameraX;
        if (x < -100 || x > WIDTH + 100) return;
        const y = GROUND_Y - 45;

        ctx.save();
        ctx.fillStyle = level.puppyColor;
        ctx.beginPath();
        ctx.roundRect(x, y, 45, 30, 8); // Body
        ctx.fill();

        ctx.roundRect(x + 30, y - 15, 20, 18, 5); // Head
        ctx.fill();

        ctx.fillStyle = level.puppyAccent;
        ctx.roundRect(x + 42, y - 20, 8, 12, 4); // Ear
        ctx.fill();

        ctx.fillStyle = "#000";
        ctx.fillRect(x + 46, y - 8, 3, 3); // Eye
        ctx.restore();
    }

    function drawPlayer() {
        const rect = playerRect();
        const x = rect.x - game.cameraX;
        const y = rect.y;

        ctx.save();
        // Hair/Back of head
        ctx.fillStyle = "#3e2723";
        ctx.beginPath();
        ctx.roundRect(x + 6, y - 24, 26, 26, 10);
        ctx.fill();

        // Face
        ctx.fillStyle = "#f3c9b1";
        ctx.beginPath();
        ctx.roundRect(player.face > 0 ? x + 12 : x + 6, y - 20, 20, 20, 4);
        ctx.fill();

        // Hair Top
        ctx.fillStyle = "#3e2723";
        ctx.fillRect(x + 6, y - 26, 26, 6);

        // Eyes
        ctx.fillStyle = "#2f1d27";
        const eyeX = player.face > 0 ? x + 24 : x + 10;
        ctx.fillRect(eyeX, y - 12, 3, 3);
        if (player.face > 0) ctx.fillRect(eyeX - 8, y - 12, 3, 3);
        else ctx.fillRect(eyeX + 8, y - 12, 3, 3);

        // Jacket/Body
        ctx.fillStyle = "#263238"; // Dark jacket
        ctx.beginPath();
        ctx.roundRect(x + 4, y, 30, rect.h - 10, 6);
        ctx.fill();

        // Shirt detail
        ctx.fillStyle = "#ff4f87";
        ctx.fillRect(x + 10, y + 4, 18, rect.h - 20);

        // Shoes
        ctx.fillStyle = "#fff";
        ctx.roundRect(x + 4, y + rect.h - 10, 12, 10, 2);
        ctx.roundRect(x + 22, y + rect.h - 10, 12, 10, 2);

        ctx.restore();
    }

    function drawTransition() {
        if (game.transitionTimer <= 0) return;
        ctx.fillStyle = "rgba(47, 29, 39, 0.7)";
        ctx.fillRect(0, 0, WIDTH, HEIGHT);
        ctx.fillStyle = "#fff";
        ctx.font = "700 48px 'Outfit', sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(game.transitionText, WIDTH / 2, HEIGHT / 2);
    }

    function drawIntroBackground() {
        ctx.fillStyle = "#fff4ea";
        ctx.fillRect(0, 0, WIDTH, HEIGHT);
        ctx.fillStyle = "rgba(255, 79, 135, 0.05)";
        for (let i = 0; i < 40; i++) {
            const x = (i * 231) % WIDTH;
            const y = (i * 147) % HEIGHT;
            ctx.beginPath();
            ctx.arc(x, y, 20, 0, Math.PI * 2);
            ctx.fill();
        }
    }

    function drawConfetti(dt) {
        if (game.confettiTimer <= 0) return;
        game.confettiTimer -= dt;
        for (const p of game.confetti) {
            p.x += p.vx * dt;
            p.y += p.vy * dt;
            p.rot += p.spin * dt;
            if (p.y > HEIGHT + 20) {
                p.y = -20;
                p.x = Math.random() * WIDTH;
            }
            ctx.save();
            ctx.translate(p.x, p.y);
            ctx.rotate(p.rot);
            ctx.fillStyle = p.color;
            ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size);
            ctx.restore();
        }
    }

    function render() {
        ctx.clearRect(0, 0, WIDTH, HEIGHT);

        if (game.mode === "intro" || game.mode === "final_question") {
            drawIntroBackground();
        }

        if (game.mode.startsWith("level_")) {
            drawBackground(activeLevel);
            drawGate(activeLevel);
            drawObstacles(activeLevel);
            drawCollectibles(activeLevel);
            drawPuppy(activeLevel);
            drawPlayer();
            drawHUD(activeLevel);
            drawTransition();
        }

        if (game.mode === "accepted") {
            drawIntroBackground();
            drawConfetti(FIXED_DT);
        }
    }

    function tick(dt) {
        if (game.mode.startsWith("level_")) updateLevel(dt);
        else if (game.mode === "final_question") updateFinalQuestion();
    }

    let last = performance.now();
    let accumulator = 0;

    function loop(now) {
        const delta = Math.min(0.05, (now - last) / 1000);
        last = now;
        accumulator += delta;

        while (accumulator >= FIXED_DT) {
            tick(FIXED_DT);
            accumulator -= FIXED_DT;
        }

        render();
        requestAnimationFrame(loop);
    }

    function mapKey(evt, isDown) {
        const k = evt.key;
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(k)) {
            evt.preventDefault();
        }
        if (k === "ArrowLeft") keys.left = isDown;
        if (k === "ArrowRight") keys.right = isDown;
        if (k === "ArrowUp") keys.up = isDown;
        if (k === "ArrowDown") keys.down = isDown;
    }

    window.addEventListener("keydown", (evt) => mapKey(evt, true));
    window.addEventListener("keyup", (evt) => mapKey(evt, false));
    startBtn.addEventListener("click", beginGame);
    yesBtn.addEventListener("click", acceptValentine);

    // Global mouse tracking on the shell for consistent dodging
    const shell = document.querySelector('.game-shell');
    shell.addEventListener("mousemove", (evt) => {
        updateFinalQuestion.lastCursor = { x: evt.clientX, y: evt.clientY };
    });

    noBtn.addEventListener("mouseover", () => {
        // Extra push on direct hover
        game.noButtonPos.x += (Math.random() - 0.5) * 100;
        game.noButtonPos.y += (Math.random() - 0.5) * 100;
    });

    requestAnimationFrame(loop);
    setMode("intro");
})();
