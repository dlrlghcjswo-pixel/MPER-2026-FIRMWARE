/**
 * 간단한 한글 2벌식 조합 + 영문 토글용 화상 키보드 헬퍼
 */
(function (global) {
  const CHO = [
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
  ];
  const JUNG = [
    "ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ",
    "ㅙ", "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ",
  ];
  const JONG = [
    "", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ",
    "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
  ];

  const COMPLEX_VOWELS = {
    "ㅗㅏ": "ㅘ", "ㅗㅐ": "ㅙ", "ㅗㅣ": "ㅚ",
    "ㅜㅓ": "ㅝ", "ㅜㅔ": "ㅞ", "ㅜㅣ": "ㅟ",
    "ㅡㅣ": "ㅢ",
  };
  const COMPLEX_FINALS = {
    "ㄱㅅ": "ㄳ", "ㄴㅈ": "ㄵ", "ㄴㅎ": "ㄶ",
    "ㄹㄱ": "ㄺ", "ㄹㅁ": "ㄻ", "ㄹㅂ": "ㄼ", "ㄹㅅ": "ㄽ",
    "ㄹㅌ": "ㄾ", "ㄹㅍ": "ㄿ", "ㄹㅎ": "ㅀ", "ㅂㅅ": "ㅄ",
  };

  function isCho(ch) {
    return CHO.indexOf(ch) !== -1;
  }
  function isJung(ch) {
    return JUNG.indexOf(ch) !== -1;
  }
  function isJong(ch) {
    return JONG.indexOf(ch) !== -1 && ch !== "";
  }

  function compose(cho, jung, jong) {
    const ci = CHO.indexOf(cho);
    const ji = JUNG.indexOf(jung);
    const ki = JONG.indexOf(jong || "");
    if (ci < 0 || ji < 0 || ki < 0) return (cho || "") + (jung || "") + (jong || "");
    return String.fromCharCode(0xac00 + ci * 21 * 28 + ji * 28 + ki);
  }

  function HangulBuffer() {
    this.cho = null;
    this.jung = null;
    this.jong = null;
  }

  HangulBuffer.prototype.text = function () {
    if (this.cho && this.jung) return compose(this.cho, this.jung, this.jong);
    if (this.cho) return this.cho;
    if (this.jung) return this.jung;
    return "";
  };

  HangulBuffer.prototype.clear = function () {
    this.cho = null;
    this.jung = null;
    this.jong = null;
  };

  HangulBuffer.prototype.empty = function () {
    return !this.cho && !this.jung && !this.jong;
  };

  function HangulComposer() {
    this.buf = new HangulBuffer();
    this.committed = "";
  }

  HangulComposer.prototype.result = function () {
    return this.committed + this.buf.text();
  };

  HangulComposer.prototype.set = function (text) {
    this.committed = text || "";
    this.buf.clear();
  };

  HangulComposer.prototype.backspace = function () {
    if (!this.buf.empty()) {
      if (this.buf.jong) this.buf.jong = null;
      else if (this.buf.jung) {
        const j = this.buf.jung;
        const parts = Object.keys(COMPLEX_VOWELS).find(function (k) {
          return COMPLEX_VOWELS[k] === j;
        });
        if (parts) this.buf.jung = parts[0];
        else this.buf.jung = null;
      } else this.buf.cho = null;
      return this.result();
    }
    this.committed = this.committed.slice(0, -1);
    return this.result();
  };

  HangulComposer.prototype.input = function (ch) {
    if (!ch) return this.result();

    // 영문/숫자/기호는 바로 확정
    if (ch.length === 1 && ch >= " " && !isCho(ch) && !isJung(ch) && !isJong(ch)) {
      this.committed += this.buf.text() + ch;
      this.buf.clear();
      return this.result();
    }

    if (isCho(ch) || isJong(ch)) {
      if (!this.buf.cho) {
        this.buf.cho = ch;
      } else if (this.buf.cho && !this.buf.jung) {
        this.committed += this.buf.cho;
        this.buf.cho = ch;
      } else if (this.buf.cho && this.buf.jung && !this.buf.jong) {
        if (isJong(ch)) this.buf.jong = ch;
        else {
          this.committed += compose(this.buf.cho, this.buf.jung, "");
          this.buf.cho = ch;
          this.buf.jung = null;
        }
      } else if (this.buf.jong) {
        const comb = COMPLEX_FINALS[this.buf.jong + ch];
        if (comb) this.buf.jong = comb;
        else {
          this.committed += compose(this.buf.cho, this.buf.jung, this.buf.jong);
          this.buf.cho = ch;
          this.buf.jung = null;
          this.buf.jong = null;
        }
      }
      return this.result();
    }

    if (isJung(ch)) {
      if (!this.buf.cho) {
        this.committed += this.buf.text();
        this.buf.clear();
        this.committed += ch;
      } else if (this.buf.cho && !this.buf.jung) {
        this.buf.jung = ch;
      } else if (this.buf.cho && this.buf.jung && !this.buf.jong) {
        const comb = COMPLEX_VOWELS[this.buf.jung + ch];
        if (comb) this.buf.jung = comb;
        else {
          this.committed += compose(this.buf.cho, this.buf.jung, "");
          this.buf.cho = null;
          this.buf.jung = ch;
        }
      } else if (this.buf.jong) {
        // 종성 분리 후 다음 음절 모음
        let move = this.buf.jong;
        const complexKey = Object.keys(COMPLEX_FINALS).find(
          function (k) {
            return COMPLEX_FINALS[k] === move;
          }
        );
        if (complexKey) {
          this.buf.jong = complexKey[0];
          move = complexKey[1];
        } else {
          this.buf.jong = null;
        }
        this.committed += compose(this.buf.cho, this.buf.jung, this.buf.jong);
        this.buf.cho = move;
        this.buf.jung = ch;
        this.buf.jong = null;
      }
      return this.result();
    }

    this.committed += this.buf.text() + ch;
    this.buf.clear();
    return this.result();
  };

  const KO_ROWS = [
    ["ㅂ", "ㅈ", "ㄷ", "ㄱ", "ㅅ", "ㅛ", "ㅕ", "ㅑ", "ㅐ", "ㅔ"],
    ["ㅁ", "ㄴ", "ㅇ", "ㄹ", "ㅎ", "ㅗ", "ㅓ", "ㅏ", "ㅣ"],
    ["ㅋ", "ㅌ", "ㅊ", "ㅍ", "ㅠ", "ㅜ", "ㅡ"],
  ];
  const EN_ROWS = [
    ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
    ["z", "x", "c", "v", "b", "n", "m"],
  ];

  function buildKeyboard(container, onKey) {
    container.innerHTML = "";
    function addRow(chars) {
      const row = document.createElement("div");
      row.className = "kb-row";
      chars.forEach(function (ch) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "kb-key";
        btn.textContent = ch;
        btn.addEventListener("click", function (e) {
          e.preventDefault();
          onKey(ch);
        });
        row.appendChild(btn);
      });
      container.appendChild(row);
    }
    return {
      render: function (lang) {
        container.innerHTML = "";
        const rows = lang === "en" ? EN_ROWS : KO_ROWS;
        rows.forEach(addRow);
      },
    };
  }

  global.HangulComposer = HangulComposer;
  global.buildKeyboard = buildKeyboard;
})(window);
