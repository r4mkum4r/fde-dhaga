// Numbers Dhaga & Co. gave us in the engagement brief. Nothing else goes in this file.
// Each one carries where it came from, so the page can say "from the brief" next to it.
// Anything worked out from these is computed in app.js with the arithmetic shown on screen.

export const BRIEF = {
  ordersPerWeek: { value: 48000, cite: "Brief §02 Scale: about forty-eight thousand orders a week" },
  returnRate: { value: 0.31, cite: "Brief §05 Neha, Category Head: returns are thirty-one percent overall" },
  otherShare: { value: 0.44, cite: "Brief §04 Returns: forty-four percent of returns land in “Other”" },
  avgOrderValue: { value: 840, cite: "Brief §02 What they sell: average order value is ₹840" },
  nehaReadsPerSitting: { text: "a few hundred at a time", cite: "Brief §05 Neha: “I can only read a few hundred at a time”" },
};

// Questions we still need the client to answer. Shown on the page, never guessed.
export const OPEN_QUESTIONS = [
  { q: "What return accuracy is good enough to act on a vendor?", owner: "Neha (Category Head)" },
  { q: "What does one customer return cost in reverse logistics?", owner: "Faizan (Head of Supply Chain)" },
  { q: "How far back does returns history go? Reviews and app events go back 18 months; returns aren't stated.", owner: "Karthik (Data analyst)" },
];
