import { useCallback, useEffect, useRef, useState } from "react";
import AboutSection from "./AboutSection";
import socials from "../../data/socials.json";
import jiyaAvatar from "../../assets/team/jiya-kumari.png";
import shivangiAvatar from "../../assets/team/shivangi-sharma.png";
import yajushAvatar from "../../assets/team/yajush-srivastava.jpg";
import kritirajAvatar from "../../assets/team/kritiraj-basumatary.png";
import kushagraAvatar from "../../assets/team/kushagra-saxena.png";
import akshatAvatar from "../../assets/team/akshat-singh.png";

type TeamMember = {
  name: string;
  role: string;
  github: string;
  avatar: string;
  quote: string;
};

const avatarByHandle: Record<string, string> = {
  "jiya-22": jiyaAvatar,
  shivangiii18: shivangiAvatar,
  "Yajush-afk": yajushAvatar,
  fuzzyKenny: kritirajAvatar,
  Kushagra0210: kushagraAvatar,
  AkshatSingh4477: akshatAvatar,
};

const quotesByHandle: Record<string, string> = {
  "jiya-22": "We built the planner to propose and the workflow to decide. The model never acts on its own.",
  shivangiii18: "Every side effect asks first. Users always know why the app is about to touch their files.",
  "Yajush-afk": "Answers cite their sources. If retrieval cannot support a claim, the app says so plainly.",
  fuzzyKenny: "Sovereign does not have to feel sparse. The interface stays calm, local, and fast.",
  Kushagra0210: "Sessions, logs, and artifacts never leave the machine. Auditability is a default, not a feature.",
  AkshatSingh4477: "Zero external calls by design. Requests have nowhere to go but the local machine.",
};

const teamMembers: TeamMember[] = socials.flatMap((s) => {
  const handle = s.github.split("/").pop() ?? "";
  const avatar = avatarByHandle[handle];
  const quote = quotesByHandle[handle];
  if (!avatar || !quote) return [];
  return [{ name: s.name, role: s.role, github: s.github, avatar, quote }];
});

const TYPE_INTERVAL_MS = 40;

export default function Team() {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const [typed, setTyped] = useState("");
  const timerRef = useRef<number | null>(null);

  const stopTyping = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setTyped("");
  }, []);

  const startTyping = useCallback((text: string) => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
    }
    let i = 0;
    const step = () => {
      i += 1;
      setTyped(text.slice(0, i));
      if (i < text.length) {
        timerRef.current = window.setTimeout(step, TYPE_INTERVAL_MS);
      }
    };
    step();
  }, []);

  const activate = useCallback(
    (index: number) => {
      setActiveIndex(index);
      startTyping(teamMembers[index].quote);
    },
    [startTyping],
  );

  const deactivate = useCallback(() => {
    setActiveIndex(null);
    stopTyping();
  }, [stopTyping]);

  useEffect(() => stopTyping, [stopTyping]);

  return (
    <AboutSection title="The Team">
      <div className="team-row">
        {teamMembers.map((m, index) => {
          const active = activeIndex === index;
          return (
            <a
              key={m.github}
              href={m.github}
              target="_blank"
              rel="noreferrer"
              className="team-member"
              aria-label={`${m.name}, ${m.role}, GitHub profile`}
              onMouseEnter={() => activate(index)}
              onMouseLeave={deactivate}
              onFocus={() => activate(index)}
              onBlur={deactivate}
            >
              <div className="team-avatar">
                <img src={m.avatar} alt="" />
              </div>
              {active && (
                <div className="team-bubble" aria-hidden="true">
                  <p className="team-bubble-quote">
                    {typed}
                    <span className="team-bubble-caret" />
                  </p>
                  <p className="team-bubble-name">{m.name}</p>
                  <p className="team-bubble-role">{m.role}</p>
                  <div className="team-bubble-dots">
                    <span />
                    <span />
                    <span />
                  </div>
                </div>
              )}
            </a>
          );
        })}
      </div>
    </AboutSection>
  );
}
