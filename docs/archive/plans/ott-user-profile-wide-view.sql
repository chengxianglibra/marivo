CREATE OR REPLACE VIEW ai.v_ott_user_profile_wide AS
WITH ott_profile AS (
    SELECT DISTINCT
        buvid,
        CASE
            WHEN predict_age_range = 0 THEN '17-'
            WHEN predict_age_range = 1 THEN '18-24'
            WHEN predict_age_range = 2 THEN '25-30'
            WHEN predict_age_range = 3 THEN '30+'
            ELSE '其他'
        END AS age,
        CASE
            WHEN predict_sex = 1 THEN '男'
            WHEN predict_sex = 2 THEN '女'
            ELSE '未知'
        END AS sex
    FROM ai.user_profile_ott
    WHERE log_date = (
        SELECT MAX(log_date)
        FROM ai.user_profile_ott
        WHERE log_date >= '20260101'
    )
)
SELECT
    base.*,
    CASE
        WHEN base.chid_day_first LIKE 'tcl%' THEN 'TCL'
        WHEN base.chid_day_first LIKE 'xiaomi%' THEN '小米'
        WHEN base.chid_day_first LIKE 'konka%' THEN '康佳'
        WHEN base.chid_day_first LIKE 'haixin%' THEN '海信'
        WHEN base.chid_day_first LIKE 'kukai%' THEN '酷开'
        WHEN base.chid_day_first LIKE 'huanshi%' THEN '欢视'
        ELSE '其他'
    END AS chid,
    CASE
        WHEN base.is_initiative_first = 1 THEN '主启'
        ELSE '外唤'
    END AS is_initiative_first_label,
    CASE
        WHEN base.is_new = 1 THEN '新用户'
        ELSE '老用户'
    END AS is_new_label,
    CASE
        WHEN base.is_login = 1 THEN '登录'
        ELSE '未登录'
    END AS is_login_label,
    CASE
        WHEN base.is_new = 1 THEN '1)当日新增'
        WHEN base.active_days_30d BETWEEN 1 AND 4 THEN '2)低活，1～4天'
        WHEN base.active_days_30d BETWEEN 5 AND 14 THEN '3)中活，5～14天'
        WHEN base.active_days_30d BETWEEN 15 AND 22 THEN '4)高活，15～22天'
        WHEN base.active_days_30d >= 23 THEN '5)极高活，23~30天'
        ELSE '6)流失回流'
    END AS active_type,
    CASE
        WHEN base.is_new = 1 THEN '1)当日新增'
        WHEN base.active_days_30d BETWEEN 1 AND 3 THEN '2)超低活，1～3天'
        WHEN base.active_days_30d BETWEEN 4 AND 10 THEN '3)低活，4～10天'
        WHEN base.active_days_30d BETWEEN 11 AND 20 THEN '4)中活，11～20天'
        WHEN base.active_days_30d BETWEEN 21 AND 26 THEN '5)高活，21～26天'
        WHEN base.active_days_30d > 26 THEN '6)极高活，27~30天'
        ELSE '7)流失回流'
    END AS active_type2,
    CASE
        WHEN base.is_dbl_buvid = 1 THEN '双栖'
        ELSE '单端'
    END AS is_dbl_buvid_label,
    CASE
        WHEN base.city_level IN ('一线', '新一线', '二线') THEN '一二线'
        WHEN base.city_level IN ('三线', '四线', '五线') THEN '三线及以下'
        ELSE '其他'
    END AS city_level2,
    CASE
        WHEN base.vv > 0 THEN '播放'
        ELSE '未播放'
    END AS is_play,
    COALESCE(profile.age, '其他') AS age,
    COALESCE(profile.sex, '未知') AS sex
FROM iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d AS base
LEFT JOIN ott_profile AS profile
    ON base.buvid = profile.buvid
WHERE base.pid = 73;
