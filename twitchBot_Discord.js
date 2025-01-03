const { Client, GatewayIntentBits, ActionRowBuilder, StringSelectMenuBuilder, InteractionType, EmbedBuilder, ButtonBuilder, ButtonStyle, SlashCommandBuilder, Routes, ClientApplication, Partials } = require('discord.js');
const tmi = require('tmi.js');
const WebSocket = require('ws');
const client = new Client({
    intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent
    ],
    partials: [Partials.Message, Partials.Channel],
});

// THIS ORIGINALLY HAS CODE FOR OTHER FUNCTIONS OF A DISCORD BOT BUT I'VE REMOVED MOST OF THEM HERE AS THEY'RE IRRELEVANT TO THE KSF BOT STUFF
// YOU WILL NEED A DISCORD BOT RUNNING ALONG SIDE THE TWITCH BOT TO CHECK FOR EMBED DATA, SINCE I'VE DELETED A MAJORITY OF THE CODE FROM HERE I MAY HAVE DELETED NORMAL STUFF YOU NEED BUT MAYBE NOT IDK
// ngl a majority of the embed data stuff was done with chatGPT cause fuck regex x)

const token = ''; // Discord bot token


// WebSocket server settings
const websocketPort = 3000;  // Port to listen on for WebSocket connections

// Setup WebSocket server for receiving messages from Twitch bot
const twitchWs = new WebSocket.Server({ port: websocketPort });

// Ensure the WebSocket server is ready before sending any data
let twitchWsConnection = null;

twitchWs.on('connection', (ws) => {
    console.log('Twitch bot connected to WebSocket!');
    twitchWsConnection = ws;

    ws.on('close', () => {
        console.log('Twitch bot disconnected from WebSocket');
        twitchWsConnection = null; // Reset the connection if it is closed
    });

    ws.on('error', (error) => {
        console.error(`WebSocket error: ${error}`);
    });
});

twitchWs.on('error', (error) => {
    console.error(`Twitch WebSocket server error: ${error}`);
});

// Replace this with the actual Discord channel ID to monitor
const targetChannelId = '';


client.on('messageCreate', async (message) => {
    // Check if the message is from the target channel
    if (message.channel.id === targetChannelId) {
        console.log(`Message received in channel ${message.channel.name}: ${message.content}`);
        
        // Check if the message contains embeds
        if (message.embeds.length > 0) {
            const embed = message.embeds[0]; // Get the first embed

            console.log('Embed found:', embed);

            // Handle the embed fields to ensure we send strings
            let embedFields = '';
            if (embed.fields && Array.isArray(embed.fields)) {
                embedFields = embed.fields.map(field => {
                    if (field.name && field.value) {
                        let formattedValue = field.value;

                        // Strip any bold formatting (remove ** characters)
                        formattedValue = formattedValue.replace(/\*\*/g, '').trim();

                        // Remove any newlines that may be present in the field value
                        formattedValue = formattedValue.replace(/\n/g, ' ').trim();

                        // Check for specific message: "More than one map found under the given input Please select the correct one below."
                        if (formattedValue === "Please select the correct one below.") {
                            console.log('Detected the selection prompt in embed field');
                            return null; // Return null or skip this field from being processed
                        }

                        // Specific logic for extracting 'Time', 'Time In Zone', 'Total Attempts' (from Completion Rate), and 'Number of Attempts'
                        if (embed.title && embed.title.includes('PR Info')) {
                            const timePattern = /Time:\s*([\d:.]+)\s*\[(\d+\/\d+)\]/;
                            const timeInZonePattern = /Time In Zone:\s*([^\n]+)/;
                            const completionRatePattern = /Completion rate:\s*([\d.]+)%\s*\((\d+)\/(\d+)\)/;

                            let timeField = '', timeInZoneField = '', totalAttemptsField = '';

                            // Check each field value for the specific patterns
                            if (timePattern.test(field.value)) {
                                // Modify to capture both time and rank in the format "Time: <time> [<rank>]"
                                const match = field.value.match(timePattern);
                                if (match) {
                                    const time = match[1];    // Extracted time (e.g., "54.884")
                                    const rank = match[2];    // Extracted rank (e.g., "4/9187")
                                    timeField = `Time: ${time} [${rank}]`;  // Combine both time and rank
                                }
                            }
                            if (timeInZonePattern.test(field.value)) {
                                timeInZoneField = field.value.match(timeInZonePattern)[1]; // Extract time in zone
                            }
                            if (completionRatePattern.test(field.value)) {
                                totalAttemptsField = field.value.match(completionRatePattern)[3]; // Extract total attempts
                            }

                            // Format the extracted fields
                            const extractedFields = [
                                timeField ? timeField : '',  // Ensure timeField includes both time and rank
                                timeInZoneField ? `Time In Zone: ${timeInZoneField}` : '',
                                totalAttemptsField ? `Total Attempts: ${totalAttemptsField}` : '', // Use total attempts here
                            ].filter(Boolean).join(' | '); // Join the fields with " | ", removing empty strings

                            return extractedFields;
                        }

                        // General case: For other embeds, apply the original pattern
                        const pattern = /Maps:\s*(\d+)\/(\d+).*Stages:\s*(\d+)\/(\d+).*Bonuses:\s*(\d+)\/(\d+).*Total:\s*(\d+\.\d+)%/;
                        if (pattern.test(formattedValue)) {
                            formattedValue = formattedValue.replace(pattern, (match, mapsCurrent, mapsTotal, stagesCurrent, stagesTotal, bonusesCurrent, bonusesTotal, total) => {
                                return `Maps: ${mapsCurrent}/${mapsTotal} | Stages: ${stagesCurrent}/${stagesTotal} | Bonuses: ${bonusesCurrent}/${bonusesTotal} | ${total}%`;
                            });
                        }

                        return `${field.name}: ${formattedValue}`;
                    }
                    return '';
                }).filter(Boolean).join(' | '); // Join the fields with " | " and filter out any null values (like the one from "Please select the correct one below.")

                // Log the final formatted message before sending it to Twitch
                console.log('Formatted Embed Field:', embedFields);

                // Ignore the case where the embed contains the message to select the correct one
                if (embedFields === "Please select the correct one below.") return;

                // Send the message to Twitch via WebSocket
                if (twitchWsConnection && twitchWsConnection.readyState === WebSocket.OPEN) {
                    twitchWsConnection.send(embedFields);
                    console.log('Embed data sent to Twitch:', embedFields);
                } else {
                    console.error('WebSocket to Twitch is not open.');
                }
            }
        } else if (message.content.trim() === "No record found.") {
            // Case where no embed is present but the message is "No record found."
            console.log('No record found message received:', message.content);

            // Send the "No record found." message to Twitch via WebSocket
            if (twitchWsConnection && twitchWsConnection.readyState === WebSocket.OPEN) {
                twitchWsConnection.send(message.content.trim());
                console.log('No record found message sent to Twitch:', message.content);
            } else {
                console.error('WebSocket to Twitch is not open.');
            }

        } else if (message.content.trim() === "There is no linked steamID to the given user.") {
            // Case where no embed is present but the message is "There is no linked steamID to the given user."
            console.log('no steamID', message.content);

            // Send a message to Twitch notifying the user via WebSocket
            if (twitchWsConnection && twitchWsConnection.readyState === WebSocket.OPEN) {
                twitchWsConnection.send('User not linked to the KSF Discord Bot');
                console.log('no steamID', message.content);
            } else {
                console.error('WebSocket to Twitch is not open.');
            }


        } else if (message.content.trim() === "Player is not online") {
            // Case where no embed is present but the message is "Player is not online."
            console.log('Player not online', message.content);
            console.log('Raw message content:', message.content);
            console.log('Trimmed message content:', message.content.trim());

            // Send the "Player is not online." message to Twitch via WebSocket
            if (twitchWsConnection && twitchWsConnection.readyState === WebSocket.OPEN) {
                twitchWsConnection.send(message.content.trim());
                console.log('player not online', message.content);
            } else {
                console.error('WebSocket to Twitch is not open.');
            }
        }
    }
});

// Handle WebSocket errors
twitchWs.on('error', (error) => {
    console.error('WebSocket error:', error);
});


client.login(token);