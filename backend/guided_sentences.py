# ============================================================
# ReVoice — Sentences for guided voice banking
# ============================================================
# The person reads these aloud one at a time. Each recording is saved
# with its exact sentence, which gives us:
#   1. a training dataset for fine-tuning XTTS on their voice (Phase 3)
#   2. clean reference clips for zero-shot cloning
#
# How the list was chosen:
#   - short, natural things a patient would really say (needs, feelings,
#     family, medical), so the recordings are useful messages in their own right
#   - a wide mix of English sounds (th, sh, ch, j, zh as in "usually",
#     ng, v, w, y, r/l clusters, oi, ow...) so the model hears every sound
#   - statements, questions and exclamations, for varied intonation
#   - no digits or symbols (numbers are spelled out) and never a "|"
#     character, because "|" separates columns in the dataset file
#
# IDs (g001, g002, ...) must NEVER change or be reused: saved recordings
# point at them. To add sentences, append new ids at the end.
# ============================================================

GUIDED_SENTENCES = [
    # ---- everyday needs ----
    ("g001", "I would like a glass of water, please."),
    ("g002", "Could you help me sit up a little?"),
    ("g003", "I need to use the bathroom."),
    ("g004", "Please turn the light off."),
    ("g005", "Can you open the window for some fresh air?"),
    ("g006", "I'm hungry. What's for lunch today?"),
    ("g007", "Could I have another blanket? I'm cold."),
    ("g008", "It's too hot in here."),
    ("g009", "Please move my pillow behind my head."),
    ("g010", "I'd like to lie down now."),
    ("g011", "Can you scratch my nose for me?"),
    ("g012", "My mouth is dry."),
    ("g013", "Please bring me my glasses."),
    ("g014", "Could you turn the television down?"),
    ("g015", "I want to brush my teeth."),
    ("g016", "Please close the curtains."),
    ("g017", "Can you charge my phone?"),
    ("g018", "I'd like some tea with a little milk."),
    ("g019", "No sugar for me, thanks."),
    ("g020", "Please wipe my face with a warm cloth."),
    ("g021", "Could you put some music on?"),
    ("g022", "I'd like to sit by the window this morning."),
    ("g023", "Please tuck my feet in."),
    ("g024", "Can I have a straw?"),
    ("g025", "Just a small spoonful, please."),

    # ---- comfort and the body ----
    ("g026", "My back is aching."),
    ("g027", "Please roll me onto my left side."),
    ("g028", "Now onto my right side, slowly."),
    ("g029", "My legs feel stiff."),
    ("g030", "Could you stretch my arm gently?"),
    ("g031", "My shoulder hurts when you lift it."),
    ("g032", "I have an itch on my cheek."),
    ("g033", "My feet are freezing."),
    ("g034", "That's much better, thank you."),
    ("g035", "Please be careful with my neck."),
    ("g036", "I feel a bit dizzy."),
    ("g037", "My eyes are tired."),
    ("g038", "Could you rub some cream on my hands?"),
    ("g039", "I'm comfortable now."),
    ("g040", "Please raise the head of the bed."),
    ("g041", "Lower it just a touch."),
    ("g042", "I'm uncomfortable. Something's pressing on my hip."),
    ("g043", "The strap is too tight."),
    ("g044", "I think I'm getting a headache."),
    ("g045", "I usually feel stronger in the morning."),

    # ---- medical ----
    ("g046", "I'm in pain. Please call the nurse."),
    ("g047", "I can't breathe properly."),
    ("g048", "It's time for my medicine."),
    ("g049", "Did I already take my tablets today?"),
    ("g050", "Please check my oxygen."),
    ("g051", "I feel sick to my stomach."),
    ("g052", "When is the doctor coming?"),
    ("g053", "I'd like to speak to the doctor myself."),
    ("g054", "Please explain that again more slowly."),
    ("g055", "Is this new medicine safe for me?"),
    ("g056", "I'm worried about the side effects."),
    ("g057", "My chest feels tight."),
    ("g058", "I need suction, please."),
    ("g059", "The machine is beeping."),
    ("g060", "I'm choking. Help me."),
    ("g061", "That injection really stung."),
    ("g062", "Could you measure my temperature?"),
    ("g063", "I didn't sleep well last night."),
    ("g064", "I'd like something to help me sleep."),
    ("g065", "Please write down what the doctor said."),
    ("g066", "Which hospital are we going to?"),
    ("g067", "I have an appointment on Thursday."),
    ("g068", "Please don't give me that, I'm allergic."),
    ("g069", "The pain is worse than yesterday."),
    ("g070", "The pain is easing off now."),

    # ---- feelings ----
    ("g071", "I'm feeling happy today."),
    ("g072", "I'm a little sad this evening."),
    ("g073", "I'm frustrated, but I'll be okay."),
    ("g074", "That really made me laugh!"),
    ("g075", "I'm scared. Please stay with me."),
    ("g076", "I feel calm and peaceful."),
    ("g077", "I'm so tired of waiting."),
    ("g078", "I'm proud of you."),
    ("g079", "Please be patient with me."),
    ("g080", "I'm sorry I snapped at you."),
    ("g081", "I'm bored. Can we do something fun?"),
    ("g082", "I feel lonely when everyone leaves."),
    ("g083", "That's wonderful news!"),
    ("g084", "I'm nervous about tomorrow."),
    ("g085", "I just need a moment to myself."),
    ("g086", "Honestly, I'm fine. Don't worry."),
    ("g087", "I'm really grateful for everything you do."),
    ("g088", "Oh no, that's awful."),
    ("g089", "I'm excited to see everyone this weekend."),
    ("g090", "Thank you for listening to me."),

    # ---- family and friends ----
    ("g091", "I love you very much."),
    ("g092", "Give me a hug."),
    ("g093", "How was your day at school?"),
    ("g094", "Tell me about your new job."),
    ("g095", "Please call my daughter."),
    ("g096", "Can my son visit this afternoon?"),
    ("g097", "Say hello to your mother for me."),
    ("g098", "Happy birthday, my darling!"),
    ("g099", "I miss our walks by the river."),
    ("g100", "You look lovely in that jumper."),
    ("g101", "Did the children eat their vegetables?"),
    ("g102", "Remember to feed the dog."),
    ("g103", "Bring the photo album next time."),
    ("g104", "Who was on the phone just now?"),
    ("g105", "Let's watch a film together tonight."),
    ("g106", "Drive carefully on your way home."),
    ("g107", "Goodnight. Sleep well."),
    ("g108", "Good morning! Did you sleep okay?"),
    ("g109", "I'm so glad you came."),
    ("g110", "Please don't go yet."),
    ("g111", "Merry Christmas to all of you!"),
    ("g112", "Congratulations, I knew you could do it."),
    ("g113", "Read me a story, would you?"),
    ("g114", "Your grandmother would be very proud."),
    ("g115", "Come and sit next to me."),

    # ---- quick replies and conversation ----
    ("g116", "Yes, please."),
    ("g117", "No, thank you."),
    ("g118", "Maybe later."),
    ("g119", "I don't know."),
    ("g120", "Wait a minute."),
    ("g121", "Could you repeat that?"),
    ("g122", "I didn't understand."),
    ("g123", "That's right, exactly."),
    ("g124", "No, that's not what I meant."),
    ("g125", "Give me a few seconds to spell it."),
    ("g126", "Let me finish my sentence, please."),
    ("g127", "What time is it?"),
    ("g128", "What day is it today?"),
    ("g129", "Why are you whispering?"),
    ("g130", "Where are my keys?"),
    ("g131", "How much longer will it take?"),
    ("g132", "Excuse me, can I ask you something?"),
    ("g133", "Of course you can."),
    ("g134", "I agree with you."),
    ("g135", "Let's talk about it tomorrow."),

    # ---- daily life and extra sounds ----
    ("g136", "The garden looks beautiful in the sunshine."),
    ("g137", "Is it raining outside?"),
    ("g138", "I'd love to go to the beach in July."),
    ("g139", "Who won the football match?"),
    ("g140", "Put the news on at six o'clock."),
    ("g141", "This soup is delicious."),
    ("g142", "The cheese sandwich was a bit stale."),
    ("g143", "Please water the plants on Wednesday."),
    ("g144", "We should order pizza for the boys."),
    ("g145", "A cup of coffee would be a real pleasure."),
    ("g146", "The noise from the road is annoying."),
    ("g147", "Have you seen my blue jacket?"),
    ("g148", "Let's go outside for a while."),
    ("g149", "I enjoy the quiet in the evening."),
    ("g150", "Thank you all for helping me find my voice."),
]

# quick lookup: id -> sentence
SENTENCE_BY_ID = dict(GUIDED_SENTENCES)
