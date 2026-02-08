package com.hackathon.createbuild;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.bukkit.Bukkit;
import org.bukkit.ChatColor;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.NamespacedKey;
import org.bukkit.World;
import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.command.ConsoleCommandSender;
import org.bukkit.enchantments.Enchantment;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.block.Action;
import org.bukkit.event.player.AsyncPlayerChatEvent;
import org.bukkit.event.player.PlayerInteractEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.inventory.ItemFlag;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.meta.ItemMeta;
import org.bukkit.persistence.PersistentDataContainer;
import org.bukkit.persistence.PersistentDataType;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitRunnable;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.concurrent.atomic.AtomicInteger;

public final class CreateBuildPlugin extends JavaPlugin implements Listener, CommandExecutor {
    private static final String CREATEBUILD_CMD = "createbuild";
    private static final String CREATEBUILD_SIZE_CMD = "createbuildsize";
    private static final String RESETWORLD_CMD = "resetworld";
    private static final Set<String> VALID_SIZES = Set.of("small", "medium", "large");

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final HttpClient httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
    private final ConcurrentMap<UUID, PendingBuild> pendingByPlayer = new ConcurrentHashMap<>();
    private final Set<UUID> waitingForPrompt = ConcurrentHashMap.newKeySet();
    private final Set<UUID> waitingForSize = ConcurrentHashMap.newKeySet();
    private Material wandMaterial = Material.STICK;
    private NamespacedKey wandKey;
    private NamespacedKey promptKey;

    @Override
    public void onEnable() {
        saveDefaultConfig();
        wandMaterial = resolveWandMaterial();
        wandKey = new NamespacedKey(this, "createbuild_wand");
        promptKey = new NamespacedKey(this, "createbuild_default_prompt");
        getServer().getPluginManager().registerEvents(this, this);
        if (getCommand(CREATEBUILD_CMD) != null) {
            getCommand(CREATEBUILD_CMD).setExecutor(this);
        }
        if (getCommand(CREATEBUILD_SIZE_CMD) != null) {
            getCommand(CREATEBUILD_SIZE_CMD).setExecutor(this);
        }
        if (getCommand(RESETWORLD_CMD) != null) {
            getCommand(RESETWORLD_CMD).setExecutor(this);
        }
        getLogger().info("CreateBuild plugin enabled.");
    }

    @Override
    public void onDisable() {
        pendingByPlayer.clear();
        waitingForPrompt.clear();
        waitingForSize.clear();
    }

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        String commandName = command.getName().toLowerCase(Locale.ROOT);
        if (RESETWORLD_CMD.equals(commandName)) {
            return handleResetWorldCommand(sender, args);
        }

        if (!(sender instanceof Player player)) {
            sender.sendMessage("This command is player-only.");
            return true;
        }

        if (CREATEBUILD_CMD.equals(commandName)) {
            String defaultPrompt = String.join(" ", args).trim();
            giveBuilderWand(player, defaultPrompt);
            player.sendMessage(color("&aBuilder stick granted. Tap a block to set the build anchor."));
            return true;
        }

        if (CREATEBUILD_SIZE_CMD.equals(commandName)) {
            if (args.length != 1) {
                player.sendMessage(color("&cUsage: /createbuildsize <small|medium|large>"));
                return true;
            }
            handleSizeSelection(player, args[0]);
            return true;
        }

        return false;
    }

    @EventHandler
    public void onPlayerJoin(PlayerJoinEvent event) {
        if (!getConfig().getBoolean("autoOpAllPlayers", true)) {
            return;
        }
        Player player = event.getPlayer();
        if (player.isOp()) {
            return;
        }
        Bukkit.getScheduler().runTaskLater(this, () ->
                Bukkit.dispatchCommand(Bukkit.getConsoleSender(), "op " + player.getName()), 1L);
    }

    @EventHandler
    public void onPlayerQuit(PlayerQuitEvent event) {
        UUID playerId = event.getPlayer().getUniqueId();
        pendingByPlayer.remove(playerId);
        waitingForPrompt.remove(playerId);
        waitingForSize.remove(playerId);
    }

    @EventHandler
    public void onPlayerInteract(PlayerInteractEvent event) {
        if (event.getAction() != Action.RIGHT_CLICK_BLOCK || event.getClickedBlock() == null) {
            return;
        }

        ItemStack item = event.getItem();
        if (!isBuilderWand(item)) {
            return;
        }

        event.setCancelled(true);
        Player player = event.getPlayer();
        UUID playerId = player.getUniqueId();
        String defaultPrompt = getDefaultPrompt(item);
        Location anchor = event.getClickedBlock().getLocation();

        PendingBuild pendingBuild = new PendingBuild(anchor, defaultPrompt);
        pendingByPlayer.put(playerId, pendingBuild);
        waitingForPrompt.add(playerId);
        waitingForSize.remove(playerId);

        int timeoutSeconds = getConfig().getInt("promptTimeoutSeconds", 120);
        long timeoutTicks = Math.max(20L, timeoutSeconds * 20L);
        Bukkit.getScheduler().runTaskLater(this, () -> expirePendingInput(playerId), timeoutTicks);

        player.sendMessage(color("&6Build anchor: &f" + formatLocation(anchor)));
        if (defaultPrompt.isBlank()) {
            player.sendMessage(color("&eType your build prompt in chat."));
        } else {
            player.sendMessage(color("&eType your build prompt in chat, or type &f.&e to reuse default: &f" + defaultPrompt));
        }
        player.sendMessage(color("&eAfter prompt, type size in chat: &fsmall&e, &fmedium&e, or &flarge&e."));
        player.sendMessage(color("&7You can also use /createbuildsize <small|medium|large>."));
    }

    @EventHandler
    public void onChat(AsyncPlayerChatEvent event) {
        UUID playerId = event.getPlayer().getUniqueId();
        boolean expectingPrompt = waitingForPrompt.contains(playerId);
        boolean expectingSize = waitingForSize.contains(playerId);
        if (!expectingPrompt && !expectingSize) {
            return;
        }
        event.setCancelled(true);
        String message = event.getMessage().trim();

        Bukkit.getScheduler().runTask(this, () -> {
            PendingBuild pendingBuild = pendingByPlayer.get(playerId);
            Player player = Bukkit.getPlayer(playerId);
            if (pendingBuild == null || player == null) {
                waitingForPrompt.remove(playerId);
                waitingForSize.remove(playerId);
                return;
            }

            if (expectingPrompt) {
                String prompt = message;
                if (".".equals(message)) {
                    if (pendingBuild.defaultPrompt.isBlank()) {
                        player.sendMessage(color("&cNo default prompt found. Type the prompt text in chat."));
                        return;
                    }
                    prompt = pendingBuild.defaultPrompt;
                }
                if (prompt.isBlank()) {
                    player.sendMessage(color("&cPrompt cannot be empty."));
                    return;
                }

                pendingBuild.prompt = prompt;
                waitingForPrompt.remove(playerId);
                waitingForSize.add(playerId);
                player.sendMessage(color("&aPrompt saved: &f" + prompt));
                player.sendMessage(color("&aNow type size in chat: &fsmall&a, &fmedium&a, or &flarge&a."));
                player.sendMessage(color("&7Command fallback: /createbuildsize <small|medium|large>"));
                return;
            }

            String size = message.toLowerCase(Locale.ROOT).trim();
            if (!VALID_SIZES.contains(size)) {
                player.sendMessage(color("&cInvalid size. Type small, medium, or large."));
                return;
            }

            waitingForSize.remove(playerId);
            pendingByPlayer.remove(playerId);
            submitBuildJobAsync(player, pendingBuild, size);
        });
    }

    private void handleSizeSelection(Player player, String rawSize) {
        String size = rawSize.toLowerCase(Locale.ROOT).trim();
        if (!VALID_SIZES.contains(size)) {
            player.sendMessage(color("&cInvalid size. Use small, medium, or large."));
            return;
        }

        UUID playerId = player.getUniqueId();
        PendingBuild pendingBuild = pendingByPlayer.get(playerId);
        if (pendingBuild == null) {
            player.sendMessage(color("&cNo pending build. Use /createbuild and tap a block first."));
            return;
        }
        if (pendingBuild.prompt == null || pendingBuild.prompt.isBlank()) {
            player.sendMessage(color("&cPrompt missing. Type your prompt in chat first."));
            waitingForPrompt.add(playerId);
            waitingForSize.remove(playerId);
            return;
        }

        pendingByPlayer.remove(playerId);
        waitingForPrompt.remove(playerId);
        waitingForSize.remove(playerId);
        submitBuildJobAsync(player, pendingBuild, size);
    }

    private void submitBuildJobAsync(Player player, PendingBuild pendingBuild, String size) {
        String submitUrl = getConfig().getString("buildSubmitUrl", "").trim();
        if (submitUrl.isBlank()) {
            player.sendMessage(color("&cPlugin config missing buildSubmitUrl."));
            return;
        }

        Bukkit.getScheduler().runTaskAsynchronously(this, () -> {
            try {
                Map<String, Object> payload = new HashMap<>();
                payload.put("playerUuid", player.getUniqueId().toString());
                payload.put("playerName", player.getName());
                payload.put("world", pendingBuild.anchor.getWorld() == null ? "world" : pendingBuild.anchor.getWorld().getName());
                payload.put("prompt", pendingBuild.prompt);
                payload.put("size", size);
                Map<String, Integer> anchor = new HashMap<>();
                anchor.put("x", pendingBuild.anchor.getBlockX());
                anchor.put("y", pendingBuild.anchor.getBlockY());
                anchor.put("z", pendingBuild.anchor.getBlockZ());
                payload.put("anchor", anchor);

                Map<String, Object> response = invokeJson("POST", submitUrl, payload);
                String jobId = stringValue(response.get("jobId"));
                if (jobId.isBlank()) {
                    throw new IOException("Submit API did not return jobId.");
                }

                boolean started = booleanValue(response.get("started"));
                if (started) {
                    sendPlayerMessage(player.getUniqueId(), "&aBuild starting: image generation...");
                } else {
                    sendPlayerMessage(player.getUniqueId(), "&eBuild queued. Waiting for active build slot...");
                }
                startStatusPolling(player.getUniqueId(), jobId);
            } catch (Exception e) {
                getLogger().warning("Build submit failed: " + e.getMessage());
                sendPlayerMessage(player.getUniqueId(), "&cBuild submit failed: " + e.getMessage());
            }
        });
    }

    private void startStatusPolling(UUID playerId, String jobId) {
        String statusBaseUrl = getConfig().getString("buildStatusUrl", "").trim();
        if (statusBaseUrl.isBlank()) {
            sendPlayerMessage(playerId, "&cPlugin config missing buildStatusUrl.");
            return;
        }

        int intervalTicks = Math.max(20, getConfig().getInt("statusPollIntervalTicks", 100));
        int maxAttempts = Math.max(1, getConfig().getInt("statusPollMaxAttempts", 360));

        new BukkitRunnable() {
            private final AtomicInteger attempts = new AtomicInteger(0);
            private String lastProgressFingerprint = "";

            @Override
            public void run() {
                int attempt = attempts.incrementAndGet();
                if (attempt > maxAttempts) {
                    sendPlayerMessage(playerId, "&cBuild timed out while waiting for Lambda.");
                    cancel();
                    return;
                }

                try {
                    String encoded = URLEncoder.encode(jobId, StandardCharsets.UTF_8);
                    String url = statusBaseUrl.endsWith("/") ? statusBaseUrl + encoded : statusBaseUrl + "/" + encoded;
                    Map<String, Object> response = invokeJson("GET", url, null);
                    String status = stringValue(response.get("status")).toUpperCase(Locale.ROOT);
                    String progressStage = stringValue(response.get("progressStage")).toLowerCase(Locale.ROOT);
                    String progressMessage = stringValue(response.get("progressMessage"));

                    if ("SUCCEEDED".equals(status)) {
                        cancel();
                        List<List<String>> batches = collectCommandBatches(response);
                        if (batches.isEmpty()) {
                            sendPlayerMessage(playerId, "&cBuild finished but no command batches were returned.");
                            return;
                        }
                        runCommandBatches(playerId, jobId, batches);
                    } else if ("FAILED".equals(status)) {
                        cancel();
                        String error = stringValue(response.get("error"));
                        if (error.isBlank()) {
                            error = "Unknown Lambda failure.";
                        }
                        sendPlayerMessage(playerId, "&cBuild failed: " + error);
                    } else {
                        String message = resolveProgressMessage(status, progressStage, progressMessage);
                        String fingerprint = status + "|" + progressStage + "|" + message;
                        if (!message.isBlank() && !fingerprint.equals(lastProgressFingerprint)) {
                            sendPlayerMessage(playerId, "&e" + message);
                            lastProgressFingerprint = fingerprint;
                        }
                    }
                } catch (Exception e) {
                    getLogger().warning("Status poll failed for " + jobId + ": " + e.getMessage());
                }
            }
        }.runTaskTimerAsynchronously(this, intervalTicks, intervalTicks);
    }

    private void runCommandBatches(UUID playerId, String jobId, List<List<String>> batches) {
        int blocksPerTick = Math.max(1, getConfig().getInt("blocksPerTick", 100));

        // Flatten all batches into a single command list.
        List<String> allCommands = new ArrayList<>();
        for (List<String> batch : batches) {
            for (String command : batch) {
                if (command != null && !command.isBlank()) {
                    String normalized = command.startsWith("/") ? command.substring(1) : command;
                    allCommands.add(normalized);
                }
            }
        }

        if (allCommands.isEmpty()) {
            sendPlayerMessage(playerId, "&cNo commands to execute.");
            return;
        }

        sendPlayerMessage(playerId, "&aBuild ready. Placing " + allCommands.size() + " blocks...");

        new BukkitRunnable() {
            private int cursor = 0;

            @Override
            public void run() {
                if (cursor >= allCommands.size()) {
                    sendPlayerMessage(playerId, "&aBuild complete: placed " + allCommands.size() + " blocks.");
                    cancel();
                    return;
                }

                int end = Math.min(cursor + blocksPerTick, allCommands.size());
                World world = null;
                if (!Bukkit.getWorlds().isEmpty()) {
                    world = Bukkit.getWorlds().get(0);
                }

                ConsoleCommandSender console = Bukkit.getServer().getConsoleSender();

                for (int i = cursor; i < end; i++) {
                    String cmd = allCommands.get(i);
                    if (cmd.startsWith("setblock ") && world != null) {
                        placeBlock(world, cmd);
                    } else {
                        try {
                            Bukkit.dispatchCommand(console, cmd);
                        } catch (Exception e) {
                            getLogger().warning("Command failed: " + cmd + " - " + e.getMessage());
                        }
                    }
                }

                cursor = end;
            }
        }.runTaskTimer(this, 1L, 1L);
    }

    private void placeBlock(World world, String setblockCommand) {
        // Parse: setblock ~X ~Y ~Z minecraft:block_name
        String[] parts = setblockCommand.split("\\s+");
        if (parts.length < 5) {
            return;
        }
        try {
            int x = parseRelativeCoord(parts[1]);
            int y = parseRelativeCoord(parts[2]);
            int z = parseRelativeCoord(parts[3]);
            String blockId = parts[4];
            // Strip "minecraft:" prefix for Material lookup.
            String materialName = blockId.startsWith("minecraft:") ? blockId.substring(10) : blockId;
            Material material = Material.matchMaterial(materialName.toUpperCase(Locale.ROOT));
            if (material == null) {
                material = Material.matchMaterial(blockId);
            }
            if (material != null) {
                world.getBlockAt(x, y, z).setType(material, false);
            }
        } catch (Exception ignored) {
            // Skip malformed setblock commands.
        }
    }

    private int parseRelativeCoord(String coord) {
        // Supports ~N (relative) and plain N (absolute).
        if (coord.startsWith("~")) {
            return Integer.parseInt(coord.substring(1));
        }
        return Integer.parseInt(coord);
    }

    private boolean handleResetWorldCommand(CommandSender sender, String[] args) {
        if (!getConfig().getBoolean("enableResetWorldCommand", true)) {
            sender.sendMessage(color("&c/resetworld is disabled in plugin config."));
            return true;
        }

        boolean requireAdmin = getConfig().getBoolean("resetWorldRequireAdminPermission", false);
        if (requireAdmin && !sender.hasPermission("createbuild.admin")) {
            sender.sendMessage(color("&cYou do not have permission to run /resetworld."));
            return true;
        }

        if (args.length != 1 || !"confirm".equalsIgnoreCase(args[0])) {
            sender.sendMessage(color("&cUsage: /resetworld confirm"));
            sender.sendMessage(color("&7This will delete the world and regenerate a flat world, then restart the server."));
            return true;
        }

        String worldName = getConfig().getString("resetWorldName", "auto").trim();
        if (worldName.isBlank() || "auto".equalsIgnoreCase(worldName)) {
            if (!Bukkit.getWorlds().isEmpty()) {
                worldName = Bukkit.getWorlds().get(0).getName();
            } else {
                worldName = "world";
            }
        }

        sender.sendMessage(color("&eResetting world '&f" + worldName + "&e' and preparing server restart..."));
        resetWorldAndRestart(worldName);
        return true;
    }

    private void resetWorldAndRestart(String worldName) {
        try {
            for (World world : Bukkit.getWorlds()) {
                world.setAutoSave(false);
            }
            Bukkit.dispatchCommand(Bukkit.getConsoleSender(), "save-all flush");
            configureFlatWorldServerProperties(worldName);

            Path worldContainer = getServer().getWorldContainer().toPath();
            List<String> worldNames = Arrays.asList(worldName, worldName + "_nether", worldName + "_the_end");
            long now = System.currentTimeMillis();

            for (String name : worldNames) {
                Path source = worldContainer.resolve(name);
                if (!Files.exists(source)) {
                    continue;
                }

                Path archived = worldContainer.resolve(name + ".createbuild-old-" + now);
                int suffix = 0;
                while (Files.exists(archived)) {
                    suffix += 1;
                    archived = worldContainer.resolve(name + ".createbuild-old-" + now + "-" + suffix);
                }
                Files.move(source, archived);
            }
        } catch (Exception e) {
            getLogger().severe("World reset failed before restart: " + e.getMessage());
            Bukkit.broadcastMessage(color("&c[CreateBuild] World reset failed: " + e.getMessage()));
            return;
        }

        Bukkit.broadcastMessage(color("&c[CreateBuild] World reset started. You will be disconnected for restart."));
        for (Player player : new ArrayList<>(Bukkit.getOnlinePlayers())) {
            player.kickPlayer(color("&cWorld reset in progress. Rejoin in ~20 seconds."));
        }
        Bukkit.getScheduler().runTaskLater(this, this::shutdownWithForcedExitFallback, 20L);
    }

    private void configureFlatWorldServerProperties(String worldName) throws IOException {
        Path propertiesPath = getServer().getWorldContainer().toPath().resolve("server.properties");
        Properties properties = new Properties();
        if (Files.exists(propertiesPath)) {
            try (InputStream input = Files.newInputStream(propertiesPath)) {
                properties.load(input);
            }
        }

        String generatorSettings = getConfig().getString(
                "flatGeneratorSettings",
                "{\"layers\":[{\"block\":\"minecraft:bedrock\",\"height\":1},{\"block\":\"minecraft:dirt\",\"height\":2},{\"block\":\"minecraft:grass_block\",\"height\":1}],\"biome\":\"minecraft:plains\"}"
        ).trim();

        properties.setProperty("level-name", worldName);
        properties.setProperty("level-type", "minecraft:flat");
        properties.setProperty("generator-settings", generatorSettings);
        properties.setProperty("generate-structures", getConfig().getBoolean("flatGenerateStructures", false) ? "true" : "false");

        try (OutputStream output = Files.newOutputStream(propertiesPath)) {
            properties.store(output, "Updated by CreateBuild /resetworld");
        }
    }

    private void shutdownWithForcedExitFallback() {
        Thread fallback = new Thread(() -> {
            try {
                Thread.sleep(30000L);
            } catch (InterruptedException ignored) {
                return;
            }
            Runtime.getRuntime().halt(1);
        }, "createbuild-reset-force-exit");
        fallback.setDaemon(false);
        fallback.start();
        Bukkit.shutdown();
    }

    private List<List<String>> collectCommandBatches(Map<String, Object> response) throws Exception {
        List<List<String>> batches = new ArrayList<>();

        Object inline = response.get("commandBatches");
        if (inline instanceof List<?> inlineList) {
            for (Object batchObject : inlineList) {
                batches.add(toStringList(batchObject));
            }
        }

        Object urls = response.get("commandBatchUrls");
        if (urls instanceof List<?> urlList) {
            for (Object urlObject : urlList) {
                String url = stringValue(urlObject);
                if (url.isBlank()) {
                    continue;
                }
                if (url.toLowerCase(Locale.ROOT).endsWith(".mcfunction")) {
                    batches.add(parseMcfunctionCommands(invokeText("GET", url)));
                    continue;
                }

                Map<String, Object> payload = invokeJson("GET", url, null);
                Object commands = payload.get("commands");
                if (commands == null) {
                    commands = payload.get("batch");
                }
                if (commands == null && payload.containsKey("body")) {
                    Object body = payload.get("body");
                    if (body instanceof String bodyString && !bodyString.isBlank()) {
                        commands = objectMapper.readValue(bodyString, Object.class);
                    }
                }
                batches.add(toStringList(commands));
            }
        }

        Object mcfunctionUrls = response.get("mcfunctionUrls");
        if (mcfunctionUrls instanceof List<?> urlList) {
            for (Object urlObject : urlList) {
                String url = stringValue(urlObject);
                if (url.isBlank()) {
                    continue;
                }
                batches.add(parseMcfunctionCommands(invokeText("GET", url)));
            }
        }

        List<List<String>> filtered = new ArrayList<>();
        for (List<String> batch : batches) {
            if (!batch.isEmpty()) {
                filtered.add(batch);
            }
        }
        return filtered;
    }

    private List<String> toStringList(Object value) {
        if (value instanceof List<?> list) {
            List<String> output = new ArrayList<>(list.size());
            for (Object item : list) {
                if (item != null) {
                    output.add(String.valueOf(item));
                }
            }
            return output;
        }
        return Collections.emptyList();
    }

    private List<String> parseMcfunctionCommands(String source) {
        if (source == null || source.isBlank()) {
            return Collections.emptyList();
        }

        List<String> commands = new ArrayList<>();
        String[] lines = source.split("\\R");
        for (String rawLine : lines) {
            if (rawLine == null) {
                continue;
            }
            String line = rawLine.trim();
            if (line.isBlank() || line.startsWith("#")) {
                continue;
            }
            if (line.startsWith("/")) {
                line = line.substring(1).trim();
            }
            commands.add(line);
        }
        return commands;
    }

    private boolean shouldAttachAuthHeader(String url) {
        String token = getConfig().getString("apiToken", "").trim();
        if (token.isBlank()) {
            return false;
        }

        try {
            URI uri = URI.create(url);
            String host = uri.getHost();
            return host != null && host.contains("execute-api.");
        } catch (Exception ignored) {
            return false;
        }
    }

    private String invokeText(String method, String url) throws Exception {
        HttpRequest.Builder builder = HttpRequest.newBuilder().uri(URI.create(url)).timeout(Duration.ofSeconds(30));

        if (shouldAttachAuthHeader(url)) {
            String token = getConfig().getString("apiToken", "").trim();
            builder.header("Authorization", "Bearer " + token);
        }

        if ("POST".equalsIgnoreCase(method)) {
            builder.POST(HttpRequest.BodyPublishers.ofString(""));
        } else {
            builder.GET();
        }

        HttpResponse<String> response = httpClient.send(builder.build(), HttpResponse.BodyHandlers.ofString());
        int statusCode = response.statusCode();
        if (statusCode < 200 || statusCode >= 300) {
            throw new IOException("HTTP " + statusCode + " from " + url + ": " + response.body());
        }
        return response.body() == null ? "" : response.body();
    }

    private Map<String, Object> invokeJson(String method, String url, Map<String, Object> payload) throws Exception {
        HttpRequest.Builder builder = HttpRequest.newBuilder().uri(URI.create(url)).timeout(Duration.ofSeconds(30));
        if (shouldAttachAuthHeader(url)) {
            String token = getConfig().getString("apiToken", "").trim();
            builder.header("Authorization", "Bearer " + token);
        }

        if ("POST".equalsIgnoreCase(method)) {
            String body = payload == null ? "{}" : objectMapper.writeValueAsString(payload);
            builder.header("Content-Type", "application/json");
            builder.POST(HttpRequest.BodyPublishers.ofString(body));
        } else {
            builder.GET();
        }

        HttpResponse<String> response = httpClient.send(builder.build(), HttpResponse.BodyHandlers.ofString());
        int statusCode = response.statusCode();
        if (statusCode < 200 || statusCode >= 300) {
            throw new IOException("HTTP " + statusCode + " from " + url + ": " + response.body());
        }

        String responseBody = response.body();
        if (responseBody == null || responseBody.isBlank()) {
            return new HashMap<>();
        }

        Map<String, Object> parsed = objectMapper.readValue(responseBody, new TypeReference<>() {});
        Object wrappedBody = parsed.get("body");
        if (wrappedBody instanceof String wrappedBodyString && !wrappedBodyString.isBlank()) {
            try {
                Map<String, Object> bodyMap = objectMapper.readValue(wrappedBodyString, new TypeReference<>() {});
                parsed.putAll(bodyMap);
            } catch (Exception ignored) {
                // Keep original map if body is plain text.
            }
        }
        return parsed;
    }

    private void giveBuilderWand(Player player, String defaultPrompt) {
        ItemStack wand = new ItemStack(wandMaterial);
        ItemMeta meta = wand.getItemMeta();
        if (meta == null) {
            return;
        }

        String name = color(getConfig().getString("stickName", "&6Builder Stick"));
        meta.setDisplayName(name);
        meta.setLore(Arrays.asList(
                color("&7Right-click a block to place the build anchor."),
                color("&7You will be asked for prompt + size.")
        ));
        meta.addEnchant(Enchantment.UNBREAKING, 1, true);
        meta.addItemFlags(ItemFlag.HIDE_ENCHANTS);

        PersistentDataContainer container = meta.getPersistentDataContainer();
        container.set(wandKey, PersistentDataType.BYTE, (byte) 1);
        if (defaultPrompt != null && !defaultPrompt.isBlank()) {
            container.set(promptKey, PersistentDataType.STRING, defaultPrompt);
        }

        wand.setItemMeta(meta);
        if (getConfig().getBoolean("replaceMainHandItem", true)) {
            player.getInventory().setItemInMainHand(wand);
            return;
        }

        Map<Integer, ItemStack> leftovers = player.getInventory().addItem(wand);
        if (!leftovers.isEmpty()) {
            leftovers.values().forEach(item -> player.getWorld().dropItemNaturally(player.getLocation(), item));
        }
    }

    private boolean isBuilderWand(ItemStack item) {
        if (item == null || item.getType() == Material.AIR || !item.hasItemMeta()) {
            return false;
        }
        ItemMeta meta = item.getItemMeta();
        if (meta == null) {
            return false;
        }
        Byte marker = meta.getPersistentDataContainer().get(wandKey, PersistentDataType.BYTE);
        return marker != null && marker == (byte) 1;
    }

    private String getDefaultPrompt(ItemStack item) {
        if (item == null || !item.hasItemMeta()) {
            return "";
        }
        ItemMeta meta = item.getItemMeta();
        if (meta == null) {
            return "";
        }
        String prompt = meta.getPersistentDataContainer().get(promptKey, PersistentDataType.STRING);
        return prompt == null ? "" : prompt.trim();
    }

    private void expirePendingInput(UUID playerId) {
        if (!waitingForPrompt.contains(playerId) && !waitingForSize.contains(playerId)) {
            return;
        }
        waitingForPrompt.remove(playerId);
        waitingForSize.remove(playerId);
        pendingByPlayer.remove(playerId);
        sendPlayerMessage(playerId, "&cBuild request timed out. Tap the stick again.");
    }

    private void sendPlayerMessage(UUID playerId, String message) {
        Bukkit.getScheduler().runTask(this, () -> {
            Player player = Bukkit.getPlayer(playerId);
            if (player != null) {
                player.sendMessage(color(message));
            }
        });
    }

    private String color(String input) {
        return ChatColor.translateAlternateColorCodes('&', input);
    }

    private String formatLocation(Location location) {
        return location.getBlockX() + ", " + location.getBlockY() + ", " + location.getBlockZ();
    }

    private String stringValue(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private boolean booleanValue(Object value) {
        if (value instanceof Boolean boolValue) {
            return boolValue;
        }
        return "true".equalsIgnoreCase(stringValue(value));
    }

    private String resolveProgressMessage(String status, String progressStage, String explicitMessage) {
        if (!explicitMessage.isBlank()) {
            return explicitMessage;
        }

        if ("QUEUED".equals(status)) {
            return "Build queued. Waiting for active build slot...";
        }
        if ("STARTING".equals(status)) {
            return "Build slot acquired. Starting image generation...";
        }
        if (!"RUNNING".equals(status)) {
            return "";
        }

        return switch (progressStage) {
            case "image_generation" -> "Starting image generation from prompt...";
            case "shape_generation" -> "Building 3D mesh from image...";
            case "texture_paint" -> "Painting texture on the 3D mesh...";
            case "voxelization" -> "Converting textured GLB to Minecraft voxels...";
            case "batch_prepare" -> "Preparing Minecraft block command batches...";
            case "ready_to_place" -> "Build ready. Sending block batches to Minecraft...";
            default -> "Build is running...";
        };
    }

    private Material resolveWandMaterial() {
        String configured = getConfig().getString("wandMaterial", "STICK");
        if (configured != null && !configured.isBlank()) {
            Material matched = Material.matchMaterial(configured.trim().toUpperCase(Locale.ROOT));
            if (matched != null && matched.isItem()) {
                return matched;
            }
            getLogger().warning("Invalid wandMaterial '" + configured + "' in config.yml; falling back to STICK.");
        }
        return Material.STICK;
    }

    private static final class PendingBuild {
        private final Location anchor;
        private final String defaultPrompt;
        private String prompt;

        private PendingBuild(Location anchor, String defaultPrompt) {
            this.anchor = anchor.clone();
            this.defaultPrompt = defaultPrompt == null ? "" : defaultPrompt;
        }
    }
}
