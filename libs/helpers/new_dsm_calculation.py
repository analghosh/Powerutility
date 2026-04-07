from decimal import Decimal, ROUND_HALF_UP, getcontext

def round_to_two(value):
    return round(value, 2)

def calculate_normal_rate(acp_dam_rs_mwh, acp_rtm_rs_mwh, ancillary_charge_rs_mwh):
    # Conversion from Rs/MWh to Rs/kWh
    acp_dam = acp_dam_rs_mwh / 10 
    acp_rtm = acp_rtm_rs_mwh / 10  
    ancillary_charge = ancillary_charge_rs_mwh / 10 

    # When Ancillary data will be available, we can include it in the calculation. For now, we will proceed with just DAM and RTM ACPs.
    # option_c = (1/3 * acp_dam) + (1/3 * acp_rtm) + (1/3 * ancillary_charge)
    option_c = (1/2 * acp_dam) + (1/2 * acp_rtm)
    nr = max(acp_dam, acp_rtm, option_c)
    return round_to_two(nr)

def get_buyer_volume_limitk(schedule_mwh, buyer_type='Standard'):
    if buyer_type == 'Standard':
        vlb1 = min(0.10 * schedule_mwh, 100 / 4)
        vlb2 = min(0.15 * schedule_mwh, 200 / 4)
    elif buyer_type == 'Small':
        vlb1 = min(0.20 * schedule_mwh, 40 / 4)
        vlb2 = float('inf')
    elif buyer_type == 'RE-rich':
        vlb1, vlb2 = 200 / 4, 300 / 4
    elif buyer_type == 'Super RE-rich':
        vlb1, vlb2 = 250 / 4, 350 / 4
    return vlb1, vlb2

def get_seller_volume_limit(scheduled_injection_mwh, seller_category='General'):
    if seller_category == 'General':
        return min(0.10 * scheduled_injection_mwh, 100 / 4)
    elif seller_category == 'RoR':
        return min(0.15 * scheduled_injection_mwh, 150 / 4)
    elif seller_category == 'MSW':
        return 0.20 * scheduled_injection_mwh
    return 0

def get_dsm_rate(entity_type, freq, nr, rr_or_contract, deviation, schedule_mwh, available_cap=None, is_wind=False):
    f = round_to_two(freq)

    if entity_type == 'Buyer':
        vlb1, vlb2 = get_buyer_volume_limitk(schedule_mwh)
        abs_dev = abs(deviation)
        
        if deviation > 0:
            if abs_dev <= vlb1:
                if f >= 50.05: return 0.75 * nr
                if f <= 49.90: return 1.50 * nr
                if f > 50.00: return nr * (1 - (f - 50.00) * 500 / 100)
                return nr * (1 + (50.00 - f) * 500 / 100)
            elif abs_dev <= vlb2:
                return 1.50 * nr if f < 50.00 else nr
            else:
                return 2.00 * nr if f < 50.00 else nr
        
        else:
            if f >= 50.05: return 0.50 * nr
            if f <= 49.90: return 1.00 * nr
            if f > 50.00: return nr * (0.9 - (f - 50.00) * 800 / 100)
            return nr * (0.9 + (50.00 - f) * 100 / 100)

    elif entity_type == 'General Seller':
        limit = get_seller_volume_limit(schedule_mwh, 'General')
        if abs(deviation) <= limit:
            if 49.97 <= f <= 50.03: return rr_or_contract
            if deviation < 0:
                if f > 50.05: return 0.85 * rr_or_contract
                if f < 49.90: return 1.50 * rr_or_contract
                if f > 50.03: return rr_or_contract * (1 - (f - 50.03) * 750 / 100)
                return rr_or_contract * (1 + (49.97 - f) * 715 / 100)
            else:
                if f > 50.05: return 0
                if f < 49.90: return 1.15 * rr_or_contract
                if f > 50.03: return rr_or_contract * (1 - (f - 50.03) * 2500 / 100)
                return rr_or_contract * (1 + (49.97 - f) * 215 / 100)
        else:
            return 2.00 * rr_or_contract if deviation < 0 and f < 49.90 else 0

    elif entity_type == 'WS Seller':
        dev_pc = (abs(deviation) / available_cap) * 100
        v1 = 15 if is_wind else 10
        v2 = 20 if is_wind else 15
        
        if deviation < 0:
            if dev_pc <= v1: return rr_or_contract
            if dev_pc <= v2: return 1.10 * rr_or_contract
            return 2.00 * rr_or_contract
        else:
            if dev_pc <= v1: return rr_or_contract
            if dev_pc <= v2: return 0.90 * rr_or_contract
            return 0

    return 0


getcontext().prec = 28

class DSMEngine:
    def __init__(self):
        pass

    def calculate_row(self, schedule_mw, actual_mw, frequency, acp_dam, acp_rtm, rr_or_contract):
        schedule_mwh = schedule_mw / 4
        actual_mwh = actual_mw / 4
        
        ancillary_charge = 0
        nr = calculate_normal_rate(acp_dam, acp_rtm, ancillary_charge)
        
        deviation = actual_mwh - schedule_mwh
        
        rate = get_dsm_rate(
            entity_type='Buyer',
            freq=frequency,
            nr=nr,
            rr_or_contract=rr_or_contract,
            deviation=deviation,
            schedule_mwh=schedule_mwh,
            available_cap=None,
            is_wind=False
        )

        S = Decimal(str(schedule_mwh))
        A_raw = Decimal(str(actual_mwh))
        F_freq = Decimal(str(frequency))
        K_rate = Decimal(str(rate))

        factors_low = {
            Decimal('49.99'): Decimal('0.0091'),
            Decimal('49.98'): Decimal('0.0092'),
            Decimal('49.97'): Decimal('0.0093'),
            Decimal('49.96'): Decimal('0.0094'),
            Decimal('49.95'): Decimal('0.0095'),
            Decimal('49.94'): Decimal('0.0096'),
            Decimal('49.93'): Decimal('0.0097'),
            Decimal('49.92'): Decimal('0.0098'),
            Decimal('49.91'): Decimal('0.0099')
        }
        factors_bb = {
            Decimal('50'): Decimal('-0.01'),
            Decimal('49.99'): Decimal('-0.0105'),
            Decimal('49.98'): Decimal('-0.011'),
            Decimal('49.97'): Decimal('-0.0115'),
            Decimal('49.96'): Decimal('-0.012'),
            Decimal('49.95'): Decimal('-0.0125'),
            Decimal('49.94'): Decimal('-0.013'),
            Decimal('49.93'): Decimal('-0.0135'),
            Decimal('49.92'): Decimal('-0.014'),
            Decimal('49.91'): Decimal('-0.0145')
        }
        factors_r = {
            Decimal('50.01'): Decimal('0.0082'),
            Decimal('50.02'): Decimal('0.0074'),
            Decimal('50.03'): Decimal('0.0066'),
            Decimal('50.04'): Decimal('0.0058'),
            Decimal('50.05'): Decimal('0.005')
        }
        factors_v = factors_r
        factors_z = factors_r
        factors_ad = factors_r

        factors_ba = {
            Decimal('50.01'): Decimal('-0.0095'),
            Decimal('50.02'): Decimal('-0.009'),
            Decimal('50.03'): Decimal('-0.0085'),
            Decimal('50.04'): Decimal('-0.008'),
            Decimal('50.05'): Decimal('-0.0075')
        }
        factors_bf = factors_ba
        factors_bj = factors_ba

        E = S * 4
        F = A_raw * 4
        G = abs(F - E)
        
        if E != 0:
            H = (G / E) * 100
        else:
            H = Decimal('0')
            
        I = (G / 4) * 1000
        L = S * 1000
        M = L * Decimal('0.05')
        N = L * Decimal('0.10')
        O = L * Decimal('0.15')
        P = L * Decimal('0.20')

        res = {
            "Schedule(MWH)": S,
            "Actual(MWH)": A_raw,
            "Schedule(MW)": E,
            "Act(MW)": F,
            "DEV": G,
            "DEV%": H,
            "DEV(Kwh)": I,
            "Freq": F_freq,
            "Rate": K_rate,
            "Sch(Kwh)": L,
            "5%sch": M,
            "10%sch": N,
            "15%sch": O,
            "20%sch": P
        }

        is_under = F < E
        is_over = F > E
        is_high_sch = E > 400
        
        Q = Decimal('0')
        if is_under and is_high_sch and I <= N:
            if F_freq == Decimal('50'):
                Q = I * K_rate * Decimal('0.009')
        res["UD_>400_10%"] = Q

        R = Decimal('0')
        if is_under and is_high_sch and I <= N:
            R = I * K_rate * factors_r.get(F_freq, Decimal('0'))
        res["UD_>400_R"] = R

        S_charge = Decimal('0')
        if is_under and is_high_sch and I <= N:
            if F_freq in factors_low:
                S_charge = I * K_rate * factors_low[F_freq]
            elif F_freq <= Decimal('49.9') and F_freq > 0:
                S_charge = I * K_rate * Decimal('0.01')
        res["UD_>400_S"] = S_charge

        T = Decimal('0')
        if is_under and is_high_sch and I <= N:
            if Decimal('50.06') <= F_freq <= Decimal('50.09'):
                T = I * K_rate * 0
            elif F_freq >= Decimal('50.1'):
                T = I * K_rate * Decimal('-0.001')
        res["UD_>400_T"] = T

        U = V = W = X = Decimal('0')
        if is_under and is_high_sch and N < I <= O:
            if F_freq == Decimal('50'):
                U = (N * K_rate * Decimal('0.009')) + ((I - N) * K_rate * Decimal('0.008'))
            if F_freq in factors_v:
                V = (N * K_rate * factors_v[F_freq]) + ((I - N) * K_rate * Decimal('0.005'))
            if F_freq in factors_low:
                W = (N * K_rate * factors_low[F_freq]) + ((I - N) * K_rate * Decimal('0.008'))
            elif F_freq <= Decimal('49.9') and F_freq > 0:
                W = (N * K_rate * Decimal('0.01')) + ((I - N) * K_rate * Decimal('0.008'))
            if Decimal('50.06') <= F_freq <= Decimal('50.09'):
                X = I * K_rate * 0
            elif F_freq >= Decimal('50.1'):
                X = I * K_rate * Decimal('-0.001')
        
        res["UD_>400_10-15%_U"] = U
        res["UD_>400_10-15%_V"] = V
        res["UD_>400_10-15%_W"] = W
        res["UD_>400_10-15%_X"] = X

        Y = Z = AA = AB = Decimal('0')
        if is_under and is_high_sch and I > O:
            if F_freq == Decimal('50'):
                Y = (N * K_rate * Decimal('0.009')) + (M * K_rate * Decimal('0.008')) + ((I - O) * 0)
            elif F_freq in factors_low:
                Y = (N * K_rate * factors_low[F_freq]) + (M * K_rate * Decimal('0.008')) + ((I - O) * 0)
            elif F_freq <= Decimal('49.9') and F_freq > 0:
                Y = (N * K_rate * Decimal('0.01')) + (M * K_rate * Decimal('0.008')) + ((I - O) * 0)
            
            if F_freq in factors_z:
                Z = (N * K_rate * factors_z[F_freq]) + (M * K_rate * Decimal('0.005')) + ((I - O) * 0)
            
            if Decimal('50.05') < F_freq <= Decimal('50.09'):
                AA = I * K_rate * 0
            
            if F_freq >= Decimal('50.1'):
                AB = I * K_rate * Decimal('-0.001')

        res["UD_>400_>15%_Y"] = Y
        res["UD_>400_>15%_Z"] = Z
        res["UD_>400_>15%_AA"] = AA
        res["UD_>400_>15%_AB"] = AB

        AC = AD = AE = AF = AG = Decimal('0')
        if is_under and not is_high_sch and I <= P and G <= 40:
            if F_freq == Decimal('50'):
                AC = I * K_rate * Decimal('0.009')
            
            AD = I * K_rate * factors_ad.get(F_freq, Decimal('0'))
            
            if F_freq in factors_low:
                AE = I * K_rate * factors_low[F_freq]
            elif F_freq <= Decimal('49.9') and F_freq > 0:
                AE = I * K_rate * Decimal('0.01')
                
            if Decimal('50.06') <= F_freq <= Decimal('50.09'):
                AF = I * K_rate * 0
            
            if F_freq >= Decimal('50.1'):
                AG = I * K_rate * Decimal('-0.001')
        
        res["UD_<=400_20%_AC"] = AC
        res["UD_<=400_20%_AD"] = AD
        res["UD_<=400_20%_AE"] = AE
        res["UD_<=400_20%_AF"] = AF
        res["UD_<=400_20%_AG"] = AG

        AZ = Decimal('0')
        if is_under and not is_high_sch:
            if I > P or G > 40:
                def calc_extra_ud(factor, extra_factor):
                    limit = Decimal('10000')
                    return (limit * K_rate * factor) + ((I - limit) * K_rate * extra_factor)

                if F_freq <= Decimal('49.9') and F_freq > 0:
                    AZ = calc_extra_ud(Decimal('0.01'), Decimal('0.008'))
                elif F_freq == Decimal('49.91'): AZ = calc_extra_ud(Decimal('0.0099'), Decimal('0.008'))
                elif F_freq == Decimal('49.92'): AZ = calc_extra_ud(Decimal('0.0098'), Decimal('0.008'))
                elif F_freq == Decimal('49.93'): AZ = calc_extra_ud(Decimal('0.0097'), Decimal('0.008'))
                elif F_freq == Decimal('49.94'): AZ = calc_extra_ud(Decimal('0.0096'), Decimal('0.008'))
                elif F_freq == Decimal('49.95'): AZ = calc_extra_ud(Decimal('0.0095'), Decimal('0.008'))
                elif F_freq == Decimal('49.96'): AZ = calc_extra_ud(Decimal('0.0094'), Decimal('0.008'))
                elif F_freq == Decimal('49.97'): AZ = calc_extra_ud(Decimal('0.0093'), Decimal('0.008'))
                elif F_freq == Decimal('49.98'): AZ = calc_extra_ud(Decimal('0.0092'), Decimal('0.008'))
                elif F_freq == Decimal('49.99'): AZ = calc_extra_ud(Decimal('0.0091'), Decimal('0.008'))
                elif F_freq == Decimal('50'): AZ = calc_extra_ud(Decimal('0.009'), Decimal('0.008'))
                elif F_freq == Decimal('50.01'): AZ = calc_extra_ud(Decimal('0.0082'), Decimal('0.005'))
                elif F_freq == Decimal('50.02'): AZ = calc_extra_ud(Decimal('0.0074'), Decimal('0.005'))
                elif F_freq == Decimal('50.03'): AZ = calc_extra_ud(Decimal('0.0066'), Decimal('0.005'))
                elif F_freq == Decimal('50.04'): AZ = calc_extra_ud(Decimal('0.0058'), Decimal('0.005'))
                elif F_freq == Decimal('50.05'): AZ = calc_extra_ud(Decimal('0.005'), Decimal('0.005'))
                elif Decimal('50.06') <= F_freq <= Decimal('50.09'):
                    AZ = I * K_rate * 0
        res["UD_<=400_>20%_AZ"] = AZ
        
        AY = Decimal('0')
        if is_under and not is_high_sch:
            if (I > P or G > 40) and F_freq >= Decimal('50.1'):
                AY = I * K_rate * Decimal('-0.001')
        res["UD_<=400_>20%_AY"] = AY

        bq_sum = Q + R + S_charge + T + U + V + W + X + Y + Z + AA + AB + AC + AD + AE + AF + AG + AZ + AY
        res["Total under drawal charges"] = -bq_sum

        BA = BB = BC = BD = Decimal('0')
        if is_over and is_high_sch and I <= N:
            BA = I * K_rate * factors_ba.get(F_freq, Decimal('0'))
            
            factors_bb = {
                Decimal('50'): Decimal('-0.01'),
                Decimal('49.99'): Decimal('-0.0105'),
                Decimal('49.98'): Decimal('-0.011'),
                Decimal('49.97'): Decimal('-0.0115'),
                Decimal('49.96'): Decimal('-0.012'),
                Decimal('49.95'): Decimal('-0.0125'),
                Decimal('49.94'): Decimal('-0.013'),
                Decimal('49.93'): Decimal('-0.0135'),
                Decimal('49.92'): Decimal('-0.014'),
                Decimal('49.91'): Decimal('-0.0145')
            }
            if F_freq in factors_bb:
                BB = I * K_rate * factors_bb[F_freq]
            elif F_freq <= Decimal('49.9') and F_freq > 0:
                BB = I * K_rate * Decimal('-0.015')
            
            if Decimal('50.05') < F_freq <= Decimal('50.09'):
                BC = I * K_rate * Decimal('-0.005')
            
            if F_freq >= Decimal('50.1'):
                BD = I * K_rate * 0
        
        res["OD_>400_10%_BA"] = BA
        res["OD_>400_10%_BB"] = BB
        res["OD_>400_10%_BC"] = BC
        res["OD_>400_10%_BD"] = BD

        BE = BF = BG = BH = Decimal('0')
        if is_over and is_high_sch and N < I <= O:
            if F_freq == Decimal('50'):
                BE = (N * K_rate * Decimal('-0.01')) + ((I - N) * K_rate * Decimal('-0.01'))
            if F_freq in factors_bf:
                BF = (N * K_rate * factors_bf[F_freq]) + ((I - N) * K_rate * Decimal('-0.01'))
            if F_freq in factors_bb:
                BG = (N * K_rate * factors_bb[F_freq]) + ((I - N) * K_rate * Decimal('-0.015'))
            elif F_freq <= Decimal('49.9') and F_freq > 0:
                BG = (N * K_rate * Decimal('-0.015')) + ((I - N) * K_rate * Decimal('-0.015'))
            if Decimal('50.05') < F_freq <= Decimal('50.09'):
                BH = (N * K_rate * Decimal('-0.005')) + ((I - N) * K_rate * Decimal('-0.0075'))
            elif F_freq >= Decimal('50.1'):
                BH = I * K_rate * 0
        
        res["OD_>400_10-15%_BE"] = BE
        res["OD_>400_10-15%_BF"] = BF
        res["OD_>400_10-15%_BG"] = BG
        res["OD_>400_10-15%_BH"] = BH

        BI = BJ = BK = BL = Decimal('0')
        if is_over and is_high_sch and I > O:
            if F_freq == Decimal('50'):
                BI = (N * K_rate * Decimal('-0.01')) + (M * K_rate * Decimal('-0.01')) + ((I - O) * K_rate * Decimal('-0.01'))
            elif F_freq in factors_bb:
                extra_od_factor = Decimal('-0.02')
                base_od_limit = Decimal('-0.015')
                if F_freq in factors_bb:
                    BI = (N * K_rate * factors_bb[F_freq]) + (M * K_rate * base_od_limit) + ((I - O) * K_rate * extra_od_factor)
                elif F_freq <= Decimal('49.9') and F_freq > 0:
                    BI = (N * K_rate * Decimal('-0.015')) + (M * K_rate * Decimal('-0.015')) + ((I - O) * K_rate * extra_od_factor)
            
            if F_freq in factors_bj:
                BJ = (N * K_rate * factors_bj[F_freq]) + (M * K_rate * Decimal('-0.01')) + ((I - O) * K_rate * Decimal('-0.01'))
            
            if Decimal('50.05') < F_freq <= Decimal('50.09'):
                BK = (N * K_rate * Decimal('-0.005')) + (M * K_rate * Decimal('-0.0075')) + ((I - O) * K_rate * Decimal('-0.01'))
            
            if F_freq >= Decimal('50.1'):
                BL = (I - O) * K_rate * Decimal('-0.005')

        res["OD_>400_>15%_BI"] = BI
        res["OD_>400_>15%_BJ"] = BJ
        res["OD_>400_>15%_BK"] = BK
        res["OD_>400_>15%_BL"] = BL

        BM = BN = BO = Decimal('0')
        if is_over and not is_high_sch and I <= P and G <= 40:
            if F_freq in factors_bb:
                BM = I * K_rate * factors_bb[F_freq]
            elif F_freq <= Decimal('49.9') and F_freq > 0:
                BM = I * K_rate * Decimal('-0.015')
            
            BN = I * K_rate * factors_ba.get(F_freq, Decimal('0'))
            
            if Decimal('50.06') <= F_freq <= Decimal('50.09'):
                BO = I * K_rate * Decimal('-0.005')
            elif F_freq >= Decimal('50.1'):
                BO = I * K_rate * 0
        
        res["OD_<=400_20%_BM"] = BM
        res["OD_<=400_20%_BN"] = BN
        res["OD_<=400_20%_BO"] = BO

        BP = Decimal('0')
        if is_over and not is_high_sch:
            if I > P or G > 40:
                def calc_extra_od(factor, extra_factor):
                    limit = Decimal('10000')
                    return (limit * K_rate * factor) + ((I - limit) * K_rate * extra_factor)

                if F_freq <= Decimal('49.9') and F_freq > 0:
                    BP = calc_extra_od(Decimal('-0.015'), Decimal('-0.015'))
                elif F_freq == Decimal('49.91'): BP = calc_extra_od(Decimal('-0.0145'), Decimal('-0.015'))
                elif F_freq == Decimal('49.92'): BP = calc_extra_od(Decimal('-0.014'), Decimal('-0.015'))
                elif F_freq == Decimal('49.93'): BP = calc_extra_od(Decimal('-0.0135'), Decimal('-0.015'))
                elif F_freq == Decimal('49.94'): BP = calc_extra_od(Decimal('-0.013'), Decimal('-0.015'))
                elif F_freq == Decimal('49.95'): BP = calc_extra_od(Decimal('-0.0125'), Decimal('-0.015'))
                elif F_freq == Decimal('49.96'): BP = calc_extra_od(Decimal('-0.012'), Decimal('-0.015'))
                elif F_freq == Decimal('49.97'): BP = calc_extra_od(Decimal('-0.0115'), Decimal('-0.015'))
                elif F_freq == Decimal('49.98'): BP = calc_extra_od(Decimal('-0.011'), Decimal('-0.015'))
                elif F_freq == Decimal('49.99'): BP = calc_extra_od(Decimal('-0.0105'), Decimal('-0.015'))
                elif F_freq == Decimal('50'): BP = calc_extra_od(Decimal('-0.01'), Decimal('-0.01'))
                elif F_freq == Decimal('50.01'): BP = calc_extra_od(Decimal('-0.0095'), Decimal('-0.01'))
                elif F_freq == Decimal('50.02'): BP = calc_extra_od(Decimal('-0.009'), Decimal('-0.01'))
                elif F_freq == Decimal('50.03'): BP = calc_extra_od(Decimal('-0.0085'), Decimal('-0.01'))
                elif F_freq == Decimal('50.04'): BP = calc_extra_od(Decimal('-0.008'), Decimal('-0.01'))
                elif F_freq == Decimal('50.05'): BP = calc_extra_od(Decimal('-0.0075'), Decimal('-0.01'))
                elif F_freq >= Decimal('50.1'):
                    BP = I * K_rate * 0
                elif Decimal('50.06') <= F_freq <= Decimal('50.09'):
                    BP = calc_extra_od(Decimal('-0.005'), Decimal('-0.0075'))

        res["OD_<=400_>20%_BP"] = BP

        br_sum = BA + BB + BC + BD + BE + BF + BG + BH + BI + BJ + BK + BL + BM + BN + BO + BP
        res["Total over drawal charges"] = -br_sum

        return res

    def get_dsm_summary(self, schedule_mw, actual_mw, frequency, acp_dam, acp_rtm, rr_or_contract):
        result = self.calculate_row(schedule_mw, actual_mw, frequency, acp_dam, acp_rtm, rr_or_contract)
        # print(result)
        # print("========================")
        # for key, val in result.items():
        #     print(f"{key} : {val}")
        if actual_mw < schedule_mw:
            net_dsm_amount = result["Total under drawal charges"]
        else:
            net_dsm_amount = result["Total over drawal charges"]
        
        return {
            'dsm_rate': float(result["Rate"]),
            'net_dsm_amount': float(net_dsm_amount)
        }

