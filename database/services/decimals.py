from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


@dataclass(frozen=True)
class ExactDecimal:
    submitted_lexeme: str
    coefficient: str
    scale: int

    @classmethod
    def parse(cls, submitted_lexeme: str) -> "ExactDecimal":
        text = submitted_lexeme.strip()
        if not text:
            raise ValueError("Decimal value cannot be blank")
        try:
            value = Decimal(text)
        except InvalidOperation as error:
            raise ValueError(f"Invalid base-10 decimal: {submitted_lexeme!r}") from error
        if not value.is_finite():
            raise ValueError("NaN and infinity are not valid commercial values")
        sign, digits, exponent = value.as_tuple()
        coefficient = "".join(map(str, digits)) or "0"
        if exponent > 0:
            coefficient += "0" * exponent
            scale = 0
        else:
            scale = -exponent
        coefficient = coefficient.lstrip("0") or "0"
        if sign and coefficient != "0":
            coefficient = "-" + coefficient
        return cls(submitted_lexeme=submitted_lexeme, coefficient=coefficient, scale=scale)

    def as_decimal(self) -> Decimal:
        return Decimal(int(self.coefficient)).scaleb(-self.scale)

    def governing_1e4(self) -> int:
        rounded = self.as_decimal().quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        scaled = rounded * 10_000
        result = int(scaled)
        if not -(1 << 63) <= result < 1 << 63:
            raise OverflowError("Four-decimal governing value exceeds signed 64-bit storage")
        return result
