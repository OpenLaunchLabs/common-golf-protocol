// Typed Common Golf Protocol messages.
use serde::{Deserialize, Deserializer, Serialize, Serializer};

pub const SERVICE: &str = "_common-golf._udp.local.";
pub const MAX_MESSAGE: usize = 65_535;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Draft {
    One,
    Two,
}

impl Draft {
    pub const SUPPORTED: [Self; 2] = [Self::Two, Self::One];

    pub fn alpn(self) -> &'static [u8] {
        match self {
            Self::One => b"common-golf/draft-1",
            Self::Two => b"common-golf/draft-2",
        }
    }

    pub fn protocol_version(self) -> &'static str {
        match self {
            Self::One => "0.1.1",
            Self::Two => "0.2.0",
        }
    }

    pub fn from_alpn(alpn: &[u8]) -> Option<Self> {
        Self::SUPPORTED
            .into_iter()
            .find(|draft| draft.alpn() == alpn)
    }

    fn excluded_metadata(self, channel: u32) -> Option<&'static str> {
        match (self, channel) {
            (Self::One, 1 | 3) => Some("estimated"),
            (Self::Two, 1) => Some("reduced_accuracy"),
            _ => None,
        }
    }
}

fn present<'de, D: Deserializer<'de>, T: Deserialize<'de>>(
    deserializer: D,
) -> Result<Option<T>, D::Error> {
    T::deserialize(deserializer).map(Some)
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Message {
    Info(Info),
    Shot(Shot),
    Club(Club),
    Status(Status),
    Control(Control),
    State(State),
    Result(ShotResult),
    Flight(Flight),
}

impl Message {
    pub fn channel(&self) -> u32 {
        match self {
            Self::Info(_) => 0,
            Self::Shot(_) => 1,
            Self::Club(_) => 3,
            Self::Status(_) => 4,
            Self::Control(_) => 5,
            Self::State(_) => 6,
            Self::Result(_) => 7,
            Self::Flight(_) => 8,
        }
    }

    pub fn decode(channel: u32, bytes: &[u8]) -> Result<Self, String> {
        if bytes.len() > MAX_MESSAGE {
            return Err("Common Golf message exceeds its limit".into());
        }
        #[derive(Deserialize)]
        struct Header {
            #[serde(default, rename = "type", deserialize_with = "present")]
            kind: Option<String>,
        }
        let header: Header = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
        let message = match (channel, header.kind.as_deref()) {
            (0, Some("info")) => serde_json::from_slice(bytes).map(Self::Info),
            (1, Some("shot")) => serde_json::from_slice(bytes).map(Self::Shot),
            (3, Some("club")) => serde_json::from_slice(bytes).map(Self::Club),
            (4, Some("status")) => serde_json::from_slice(bytes).map(Self::Status),
            (5, Some("control")) => serde_json::from_slice(bytes).map(Self::Control),
            (6, Some("state")) => serde_json::from_slice(bytes).map(Self::State),
            (7, Some("result")) => serde_json::from_slice(bytes).map(Self::Result),
            (8, Some("flight")) => serde_json::from_slice(bytes).map(Self::Flight),
            _ => return Err("Common Golf channel and message type differ".into()),
        }
        .map_err(|e| e.to_string())?;
        Ok(message)
    }

    pub fn decode_for(draft: Draft, channel: u32, bytes: &[u8]) -> Result<Self, String> {
        if bytes.len() > MAX_MESSAGE {
            return Err("Common Golf message exceeds its limit".into());
        }
        let Some(excluded) = draft.excluded_metadata(channel) else {
            return Self::decode(channel, bytes);
        };
        let mut fields: std::collections::BTreeMap<String, &serde_json::value::RawValue> =
            serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
        fields.remove(excluded);
        Self::decode(
            channel,
            &serde_json::to_vec(&fields).map_err(|e| e.to_string())?,
        )
    }

    pub fn encode(&self) -> Result<Vec<u8>, String> {
        // serde_json otherwise silently serializes non-finite floats as null.
        if contains_nonfinite(self) {
            return Err("Common Golf measurement is not finite".into());
        }
        let bytes = serde_json::to_vec(self).map_err(|e| e.to_string())?;
        if bytes.len() > MAX_MESSAGE {
            return Err("Common Golf message exceeds its limit".into());
        }
        Ok(bytes)
    }

    pub fn encode_for(&self, draft: Draft) -> Result<Vec<u8>, String> {
        let mut message = self.clone();
        match (&mut message, draft) {
            (Self::Shot(shot), Draft::One) => shot.estimated = None,
            (Self::Shot(shot), Draft::Two) => shot.reduced_accuracy = None,
            (Self::Club(club), Draft::One) => club.estimated = None,
            _ => {}
        }
        message.encode()
    }
}

fn contains_nonfinite(message: &Message) -> bool {
    let numbers: Vec<Option<f64>> = match message {
        Message::Shot(s) => vec![
            Some(s.ball_speed_meters_per_second),
            Some(s.vertical_launch_angle_degrees),
            Some(s.horizontal_launch_angle_degrees),
            s.total_spin_revolutions_per_minute,
            s.spin_axis_degrees,
            s.backspin_revolutions_per_minute,
            s.sidespin_revolutions_per_minute,
        ],
        Message::Club(s) => s.numbers(),
        Message::Flight(s) => s.numbers(),
        Message::Result(s) => s.numbers(),
        Message::Status(s) => vec![
            s.battery_percent.value().copied(),
            s.uptime_seconds.value().copied(),
        ],
        Message::State(s) => s
            .ball_zone
            .value()
            .map(|b| {
                vec![
                    Some(b.x_meters),
                    Some(b.y_meters),
                    Some(b.width_meters),
                    Some(b.height_meters),
                ]
            })
            .unwrap_or_default(),
        _ => vec![],
    };
    numbers.into_iter().flatten().any(|v| !v.is_finite())
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Dexterity {
    Right,
    Left,
    Unknown,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TeePosition {
    Tee,
    Mat,
    Ground,
    Unknown,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ShotKind {
    Full,
    Putt,
    Chip,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Info {
    pub protocol_version: String,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub client: Option<String>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub name: Option<String>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub vendor: Option<String>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub model: Option<String>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub serial: Option<String>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub firmware_version: Option<String>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub channels: Option<Vec<u32>>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Shot {
    pub shot_number: u64,
    pub ball_speed_meters_per_second: f64,
    pub vertical_launch_angle_degrees: f64,
    pub horizontal_launch_angle_degrees: f64,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub total_spin_revolutions_per_minute: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub spin_axis_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub backspin_revolutions_per_minute: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub sidespin_revolutions_per_minute: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub measurement_id: Option<String>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub measured_at_nanoseconds: Option<i128>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub player_dexterity: Option<Dexterity>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub tee_position: Option<TeePosition>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub reduced_accuracy: Option<bool>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub estimated: Option<Vec<String>>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub shot_kind: Option<ShotKind>,
    #[serde(default = "final_default", rename = "final")]
    pub final_measurement: bool,
}
fn final_default() -> bool {
    true
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Club {
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub estimated: Option<Vec<String>>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub shot_number: Option<u64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub swing_number: Option<u64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub player_dexterity: Option<Dexterity>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub club_speed_meters_per_second: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub angle_of_attack_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub club_path_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub face_angle_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub face_to_path_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub dynamic_loft_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub spin_loft_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub smash_factor: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub swing_direction_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub swing_plane_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub swing_radius_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub d_plane_tilt_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub low_point_distance_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub low_point_height_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub low_point_side_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub lie_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub speed_at_impact_meters_per_second: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub face_impact_horizontal_millimeters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub face_impact_vertical_millimeters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub closure_rate_degrees_per_second: Option<f64>,
    #[serde(default = "final_default", rename = "final")]
    pub final_measurement: bool,
}
impl Club {
    fn numbers(&self) -> Vec<Option<f64>> {
        vec![
            self.club_speed_meters_per_second,
            self.angle_of_attack_degrees,
            self.club_path_degrees,
            self.face_angle_degrees,
            self.face_to_path_degrees,
            self.dynamic_loft_degrees,
            self.spin_loft_degrees,
            self.smash_factor,
            self.swing_direction_degrees,
            self.swing_plane_degrees,
            self.swing_radius_meters,
            self.d_plane_tilt_degrees,
            self.low_point_distance_meters,
            self.low_point_height_meters,
            self.low_point_side_meters,
            self.lie_degrees,
            self.speed_at_impact_meters_per_second,
            self.face_impact_horizontal_millimeters,
            self.face_impact_vertical_millimeters,
            self.closure_rate_degrees_per_second,
        ]
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Flight {
    pub shot_number: u64,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub carry_distance_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub carry_side_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub total_distance_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub total_side_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub max_height_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub curve_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub landing_angle_degrees: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub hang_time_seconds: Option<f64>,
    #[serde(default = "final_default", rename = "final")]
    pub final_measurement: bool,
}
impl Flight {
    fn numbers(&self) -> Vec<Option<f64>> {
        vec![
            self.carry_distance_meters,
            self.carry_side_meters,
            self.total_distance_meters,
            self.total_side_meters,
            self.max_height_meters,
            self.curve_meters,
            self.landing_angle_degrees,
            self.hang_time_seconds,
        ]
    }
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct ShotResult {
    pub shot_number: u64,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub accepted: Option<bool>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub reject_reason: Option<String>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub carry_distance_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub total_distance_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub carry_side_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub total_side_meters: Option<f64>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub max_height_meters: Option<f64>,
}
impl ShotResult {
    fn numbers(&self) -> Vec<Option<f64>> {
        vec![
            self.carry_distance_meters,
            self.total_distance_meters,
            self.carry_side_meters,
            self.total_side_meters,
            self.max_height_meters,
        ]
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub enum Patch<T> {
    #[default]
    Missing,
    Clear,
    Set(T),
}
impl<T> Patch<T> {
    pub fn is_missing(&self) -> bool {
        matches!(self, Self::Missing)
    }
    pub fn value(&self) -> Option<&T> {
        if let Self::Set(value) = self {
            Some(value)
        } else {
            None
        }
    }
    pub fn apply(self, target: &mut Option<T>) {
        match self {
            Self::Missing => {}
            Self::Clear => *target = None,
            Self::Set(value) => *target = Some(value),
        }
    }
}
impl<'de, T: Deserialize<'de>> Deserialize<'de> for Patch<T> {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        Option::<T>::deserialize(d).map(|v| v.map(Self::Set).unwrap_or(Self::Clear))
    }
}
impl<T: Serialize> Serialize for Patch<T> {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        match self {
            Self::Set(value) => value.serialize(s),
            _ => s.serialize_none(),
        }
    }
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Status {
    #[serde(default, skip_serializing_if = "Patch::is_missing")]
    pub ready: Patch<bool>,
    #[serde(default, skip_serializing_if = "Patch::is_missing")]
    pub ball_detected: Patch<bool>,
    #[serde(default, skip_serializing_if = "Patch::is_missing")]
    pub battery_percent: Patch<f64>,
    #[serde(default, skip_serializing_if = "Patch::is_missing")]
    pub uptime_seconds: Patch<f64>,
    #[serde(default, skip_serializing_if = "Patch::is_missing")]
    pub message: Patch<String>,
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct State {
    #[serde(default, skip_serializing_if = "Patch::is_missing")]
    pub ready: Patch<bool>,
    #[serde(default, skip_serializing_if = "Patch::is_missing")]
    pub player: Patch<Player>,
    #[serde(default, skip_serializing_if = "Patch::is_missing")]
    pub club: Patch<SelectedClub>,
    #[serde(default, skip_serializing_if = "Patch::is_missing")]
    pub mode: Patch<Mode>,
    #[serde(default, skip_serializing_if = "Patch::is_missing")]
    pub ball_zone: Patch<BallZone>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Player {
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub name: Option<String>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub dexterity: Option<Dexterity>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct SelectedClub {
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub id: Option<String>,
    #[serde(
        default,
        deserialize_with = "present",
        skip_serializing_if = "Option::is_none"
    )]
    pub name: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct BallZone {
    pub x_meters: f64,
    pub y_meters: f64,
    pub width_meters: f64,
    pub height_meters: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Range,
    Course,
    Putting,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ControlEvent {
    #[default]
    Press,
    Down,
    Up,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Control {
    pub command: String,
    #[serde(default)]
    pub event: ControlEvent,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn accuracy_metadata_is_preserved_only_in_its_negotiated_draft() {
        let payload = json!({
            "type": "shot", "shot_number": 1, "ball_speed_meters_per_second": 40,
            "vertical_launch_angle_degrees": 10, "horizontal_launch_angle_degrees": 0,
            "reduced_accuracy": true, "estimated": ["ball_speed_meters_per_second"]
        });
        let bytes = serde_json::to_vec(&payload).unwrap();
        let canonical = Message::decode(1, &bytes).unwrap();
        for draft in Draft::SUPPORTED {
            let Message::Shot(decoded) = Message::decode_for(draft, 1, &bytes).unwrap() else {
                panic!();
            };
            assert_eq!(
                decoded.reduced_accuracy,
                (draft == Draft::One).then_some(true)
            );
            assert_eq!(
                decoded.estimated,
                (draft != Draft::One).then(|| vec!["ball_speed_meters_per_second".into()])
            );
            let wire: serde_json::Value =
                serde_json::from_slice(&canonical.encode_for(draft).unwrap()).unwrap();
            assert_eq!(wire.get("estimated").is_some(), draft != Draft::One);
            assert_eq!(wire.get("reduced_accuracy").is_some(), draft == Draft::One);
            assert_eq!(wire["ball_speed_meters_per_second"], 40.0);
        }
        let mut legacy_only = payload;
        legacy_only.as_object_mut().unwrap().remove("estimated");
        let decoded =
            Message::decode_for(Draft::Two, 1, &serde_json::to_vec(&legacy_only).unwrap()).unwrap();
        let Message::Shot(decoded) = decoded else {
            panic!()
        };
        assert_eq!(decoded.estimated, None);
    }

    #[test]
    fn estimated_club_fields_preserve_absence_and_empty_without_inventing_accuracy() {
        for estimated in [
            None,
            Some(json!([])),
            Some(json!(["club_speed_meters_per_second"])),
        ] {
            let mut payload =
                json!({"type": "club", "shot_number": 1, "club_speed_meters_per_second": 30});
            if let Some(value) = &estimated {
                payload["estimated"] = value.clone();
            }
            let message =
                Message::decode_for(Draft::Two, 3, &serde_json::to_vec(&payload).unwrap()).unwrap();
            let wire: serde_json::Value =
                serde_json::from_slice(&message.encode_for(Draft::Two).unwrap()).unwrap();
            assert_eq!(wire.get("estimated"), estimated.as_ref());
            let legacy: serde_json::Value =
                serde_json::from_slice(&message.encode_for(Draft::One).unwrap()).unwrap();
            assert!(legacy.get("estimated").is_none());
            assert_eq!(legacy["club_speed_meters_per_second"], 30.0);
        }
        for estimated in [json!(null), json!(true), json!([1])] {
            let bytes = serde_json::to_vec(&json!({"type":"club", "estimated":estimated})).unwrap();
            assert!(Message::decode_for(Draft::Two, 3, &bytes).is_err());
            assert!(Message::decode_for(Draft::One, 3, &bytes).is_ok());
        }
    }

    #[test]
    fn draft_filtering_preserves_full_timestamp_precision() {
        let bytes = br#"{"type":"shot","shot_number":1,"ball_speed_meters_per_second":40,"vertical_launch_angle_degrees":10,"horizontal_launch_angle_degrees":0,"measured_at_nanoseconds":18446744073709551617}"#;
        for draft in Draft::SUPPORTED {
            let message = Message::decode_for(draft, 1, bytes).unwrap();
            let round_trip =
                Message::decode_for(draft, 1, &message.encode_for(draft).unwrap()).unwrap();
            let Message::Shot(shot) = round_trip else {
                panic!()
            };
            assert_eq!(
                shot.measured_at_nanoseconds,
                Some(18_446_744_073_709_551_617)
            );
        }
    }

    #[test]
    fn typed_messages_use_decoding_errors_without_semantic_validation() {
        let bytes = br#"{"type":"shot","shot_number":0,"ball_speed_meters_per_second":-1,"vertical_launch_angle_degrees":100,"horizontal_launch_angle_degrees":0,"estimated":["missing"],"future":true}"#;
        let message = Message::decode(1, bytes).unwrap();
        let encoded = message.encode().unwrap();
        let value: serde_json::Value = serde_json::from_slice(&encoded).unwrap();
        assert_eq!(value["ball_speed_meters_per_second"], -1.0);
        assert_eq!(value["vertical_launch_angle_degrees"], 100.0);
        assert!(value.get("future").is_none());
        assert!(Message::decode(1, br#"{"type":"shot"}"#).is_err());
        assert!(Message::decode(1, br#"{"type":"shot","shot_number":"one"}"#).is_err());
        if let Message::Shot(mut shot) = message {
            shot.ball_speed_meters_per_second = f64::INFINITY;
            assert!(Message::Shot(shot).encode().is_err());
        }
    }
}
