import { describe, it, expect } from 'vitest';
import { filterPhoneInput, isValidPhone } from './phone';

describe('filterPhoneInput', () => {
    it('keeps digits and common phone punctuation', () => {
        expect(filterPhoneInput('+1 (555) 123-4567')).toBe('+1 (555) 123-4567');
        expect(filterPhoneInput('555.123.4567 x89')).toBe('555.123.4567 x89');
    });

    it('keeps letters, so extensions and vanity numbers survive typing', () => {
        expect(filterPhoneInput('555 123 4567 ext. 12')).toBe('555 123 4567 ext. 12');
        expect(filterPhoneInput('1-800-GOT-JUNK')).toBe('1-800-GOT-JUNK');
    });

    it('keeps digits and letters from any script, not just ASCII', () => {
        // The portal ships a 100+ language selector; an ASCII allowlist ate
        // these keystroke by keystroke as a resident typed them.
        expect(filterPhoneInput('٥٥٥ ١٢٣ ٤٥٦٧')).toBe('٥٥٥ ١٢٣ ٤٥٦٧');
        expect(filterPhoneInput('+७ ९१२ ३४५ ६७८९')).toBe('+७ ९१२ ३४५ ६७८९');
        expect(filterPhoneInput('+7 495 123 4567 доб. 12')).toBe('+7 495 123 4567 доб. 12');
        expect(filterPhoneInput('+81 3-1234-5678 内線 12')).toBe('+81 3-1234-5678 内線 12');
    });

    it('drops special characters as typed', () => {
        expect(filterPhoneInput('<script>5551234567</script>')).toBe('script5551234567script');
        expect(filterPhoneInput('555_123@4567!')).toBe('5551234567');
        expect(filterPhoneInput('555;123"4567')).toBe('5551234567');
        expect(filterPhoneInput('555 123 4567 📞')).toBe('555 123 4567 ');
    });
});

describe('filter and validator agree', () => {
    // Whatever the field lets a resident type has to be submittable: a value
    // that survives filtering but fails validation is a field they cannot fix.
    const typeable = [
        '+1 (555) 123-4567',
        '555.123.4567 x89',
        '555 123 4567 ext. 12',
        '1-800-GOT-JUNK',
        '٥٥٥ ١٢٣ ٤٥٦٧',
        '+7 495 123 4567 доб. 12',
        '(+44) 20 7946 0958',
    ];
    it.each(typeable)('accepts %s, which the filter preserves verbatim', (value) => {
        expect(filterPhoneInput(value)).toBe(value);
        expect(isValidPhone(value)).toBe(true);
    });
});

describe('isValidPhone', () => {
    it('accepts an empty value because the field is optional', () => {
        expect(isValidPhone('')).toBe(true);
        expect(isValidPhone('   ')).toBe(true);
    });

    it('accepts common US and international formats', () => {
        expect(isValidPhone('(555) 123-4567')).toBe(true);
        expect(isValidPhone('5551234567')).toBe(true);
        expect(isValidPhone('+1 555 123 4567')).toBe(true);
        expect(isValidPhone('+44 20 7946 0958')).toBe(true);
        expect(isValidPhone('(+44) 20 7946 0958')).toBe(true);
        expect(isValidPhone('555-123-4567 x22')).toBe(true);
        expect(isValidPhone('555 123 4567 ext. 12')).toBe(true);
    });

    it('accepts exactly 7 and exactly 15 digits, the boundary lengths', () => {
        expect(isValidPhone('1234567')).toBe(true);
        expect(isValidPhone('123456789012345')).toBe(true);
        expect(isValidPhone('123456')).toBe(false);
        expect(isValidPhone('1234567890123456')).toBe(false);
    });

    it('rejects values without enough digits or with stray characters', () => {
        expect(isValidPhone('12345')).toBe(false);
        expect(isValidPhone('call me maybe')).toBe(false);
        expect(isValidPhone('555@123$4567')).toBe(false);
        expect(isValidPhone('55+5123456')).toBe(false);   // + after a digit
        expect(isValidPhone('+1+5551234567')).toBe(false); // more than one +
    });
});
