use std::io::{self, Read};
fn main() {
    let mut text = String::new();
    io::stdin().read_to_string(&mut text).unwrap();
    for line in text.lines() { println!("{}", line.parse::<u32>().unwrap_or(0)); }
}
