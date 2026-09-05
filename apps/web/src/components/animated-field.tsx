function AnimatedField() {
  return (
    <div className="motion-field" aria-hidden="true">
      <svg className="flow-lines" viewBox="0 0 700 500" preserveAspectRatio="xMidYMid slice">
        <defs>
          <linearGradient id="flowGradient" x1="0" x2="1">
            <stop offset="0" stopColor="#8B5CF6" stopOpacity=".05" />
            <stop offset=".5" stopColor="#8B5CF6" stopOpacity=".3" />
            <stop offset="1" stopColor="#A78BFA" stopOpacity=".05" />
          </linearGradient>
        </defs>
        <g fill="none" stroke="url(#flowGradient)" strokeWidth="1">
          <path d="M-40 60 C170 90 230 230 350 250 S530 130 740 70" />
          <path d="M-40 100 C170 120 230 235 350 250 S530 155 740 100" />
          <path d="M-40 140 C165 150 240 240 350 250 S535 180 740 130" />
          <path d="M-40 180 C165 180 245 245 350 250 S540 205 740 165" />
          <path d="M-40 220 C170 215 250 248 350 250 S540 225 740 200" />
          <path d="M-40 280 C170 285 250 252 350 250 S540 275 740 300" />
          <path d="M-40 320 C165 320 245 258 350 250 S535 295 740 335" />
          <path d="M-40 360 C165 350 240 260 350 250 S530 320 740 370" />
          <path d="M-40 400 C170 380 230 265 350 250 S530 345 740 405" />
          <path d="M-40 440 C170 410 230 270 350 250 S530 370 740 440" />
        </g>
      </svg>
      <div className="motion-grid" />
    </div>
  );
}

export default AnimatedField;
